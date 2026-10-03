"""Secret-safe application env editing using python-dotenv's existing parser."""

from collections.abc import Mapping
from datetime import datetime, timezone
from io import StringIO
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
import stat
import tempfile
from uuid import uuid4

from dotenv.parser import parse_stream

SECRET_FIELDS = ("DEEPSEEK_API_KEY", "SILICONFLOW_API_KEY", "SI_ONEBOT_ACCESS_TOKEN")
EDITABLE_FIELDS = frozenset(SECRET_FIELDS) | {
    "SI_LLM_API_URL",
    "SI_LLM_MODEL",
    "SI_MEMORY_EMBEDDING_API_URL",
    "SI_MEMORY_EMBEDDING_API_ID",
    "SI_MEMORY_EMBEDDING_DIMENSION",
    "SI_MEMORY_RERANKER_API_URL",
    "SI_MEMORY_EMBEDDING_PROVIDER",
    "SI_MEMORY_EMBEDDING_MODEL",
    "SI_MEMORY_RERANKER_PROVIDER",
    "SI_MEMORY_RERANKER_MODEL",
    "SI_CHAT_TRANSPORT",
    "SI_ONEBOT_WS_URL",
    "SI_QQ_BOT_USER_ID",
    "SI_QQ_ALLOWED_USER_IDS",
}


class EnvEditError(RuntimeError):
    """Contains safe diagnostics only, never input values or underlying errors."""


def protect_file(path: Path) -> None:
    """Protect secrets for the current operator; no account or ownership changes."""
    if os.name == "posix":
        os.chmod(path, 0o600)


def parse_env(text: str) -> dict[str, str]:
    bindings = list(parse_stream(StringIO(text)))
    if any(item.error for item in bindings):
        raise EnvEditError("Invalid env syntax; review file in a trusted editor.")
    return {item.key: item.value or "" for item in bindings if item.key is not None}


class ApplicationEnvService:
    def __init__(self, path: Path, template: Path) -> None:
        self.path, self.template = path, template

    def _text(self) -> str:
        if self.path.is_symlink():
            raise EnvEditError("Refusing symlinked environment file.")
        if not self.path.exists():
            return ""
        if not stat.S_ISREG(self.path.stat().st_mode):
            raise EnvEditError("Environment path must be a regular file.")
        return self.path.read_text(encoding="utf-8")

    def read(self) -> dict[str, str]:
        try:
            return parse_env(self._text())
        except Exception:
            raise EnvEditError(
                "Unable to read application configuration safely."
            ) from None

    def defaults(self) -> dict[str, str]:
        try:
            return parse_env(self.template.read_text(encoding="utf-8"))
        except Exception:
            raise EnvEditError("Unable to read application template.") from None

    def get_status(self) -> str:
        if not self.path.exists():
            return "Missing"
        values = self.read()
        required = (
            "DEEPSEEK_API_KEY",
            "SILICONFLOW_API_KEY",
            "SI_RUNTIME_DB",
            "SI_MEMORY_EMBEDDING_MODEL",
            "SI_MEMORY_RERANKER_MODEL",
            "SI_ONEBOT_WS_URL",
            "SI_QQ_BOT_USER_ID",
            "SI_QQ_ALLOWED_USER_IDS",
        )
        return (
            "Configured"
            if all(values.get(key, "").strip() for key in required)
            and (
                values.get("SI_MEMORY_EMBEDDING_PROVIDER") == "api"
                and values.get("SI_MEMORY_RERANKER_PROVIDER") == "api"
                and values.get("SI_CHAT_TRANSPORT") == "qq"
            )
            else "Incomplete"
        )

    def backup(self, original: str) -> Path:
        target = self.path.with_name(
            f"{self.path.name}.bak.{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.{uuid4().hex[:8]}"
        )
        # Unique names plus x mode preserve every rapid successive Web save.
        with target.open("x", encoding="utf-8", newline="\n") as file:
            protect_file(target)
            file.write(original)
            file.flush()
            os.fsync(file.fileno())
        return target

    def atomic_write(self, text: str, *, expected: str) -> None:
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=".si-env-", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                protect_file(temporary)  # Protect BEFORE writing any credentials.
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            if self._text() != expected:
                raise EnvEditError("Configuration changed; reopen setup before saving.")
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def update(self, values: Mapping[str, str], *, expected: str) -> Path | None:
        try:
            if set(values) - EDITABLE_FIELDS:
                raise EnvEditError("Unsupported configuration field.")
            if any(
                not isinstance(value, str) or any(not c.isprintable() for c in value)
                for value in values.values()
            ):
                raise EnvEditError(
                    "Configuration values must be single-line printable text."
                )
            original = self._text()
            if original != expected:
                raise EnvEditError("Configuration changed; reopen setup before saving.")
            base = original or self.template.read_text(encoding="utf-8")
            parse_env(base)
            # Fill absent application-profile fields, never overwrite existing paths/unknown keys.
            existing = parse_env(base)
            pending = {
                key: value
                for key, value in self.defaults().items()
                if key not in existing
            } | dict(values)
            pieces = []
            for item in parse_stream(StringIO(base)):
                if item.key is not None and item.key in values:
                    # Replace all duplicate definitions consistently. Preserve other bindings verbatim.
                    pieces.append(
                        f"{item.key}={json.dumps(values[item.key], ensure_ascii=False)}\n"
                    )
                    pending.pop(item.key, None)
                else:
                    pieces.append(item.original.string)
            text = "".join(pieces)
            if text and not text.endswith("\n"):
                text += "\n"
            text += "".join(
                f"{key}={json.dumps(value, ensure_ascii=False)}\n"
                for key, value in pending.items()
            )
            backup = self.backup(original) if self.path.exists() else None
            self.atomic_write(text, expected=original)
            return backup
        except Exception:
            raise EnvEditError(
                "Configuration save failed; original file preserved unless replacement completed. Check permissions/concurrent edits."
            ) from None


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    message: str
    exit_code: int | None = None


def redact(text: str, environment: Mapping[str, str]) -> str:
    """Redact configured credentials plus common credential log formats."""
    secrets = sorted(
        {
            value
            for name, value in environment.items()
            if value
            and any(
                word in name.upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")
            )
        },
        key=len,
        reverse=True,
    )
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*).+", r"\1[REDACTED]", text)
    text = re.sub(
        r"(?i)((?:[\w-]*(?:api[_-]?key|token|password|secret))\s*[:=]\s*)[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    # Config values must never inject terminal control sequences.
    return "".join(c for c in text if c in "\n\t" or (c.isprintable() and c != "\x1b"))
