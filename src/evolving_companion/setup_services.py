"""UI-independent first-run configuration; check saved files in an isolated process."""

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.runtime_config import RuntimePaths
from evolving_companion.config_env import (
    ApplicationEnvService,
    EnvEditError,
    SECRET_FIELDS,
)
from evolving_companion.config_env import OperationResult, redact
from evolving_companion.local_env import PROJECT_ROOT
from evolving_companion.qq_adapter import _identifier
from evolving_companion.ui_text import check_text


@dataclass(frozen=True)
class SetupDetection:
    status: str
    character: str
    internal_identity: str
    message: str


@dataclass(frozen=True)
class SetupResult:
    saved: bool
    ready: bool
    message: str


class SetupService:
    def __init__(
        self,
        env_file: Path,
        environment: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> None:
        # Capture explicit OS variables BEFORE entry loads any env file.
        self.environment = dict(os.environ if environment is None else environment)
        project = (project_root or PROJECT_ROOT).resolve()
        self.env = ApplicationEnvService(env_file, project / "config/si.env.example")
        self._original = self.env._text()
        self._existing = self.env.read()
        self._defaults = self.env.defaults()
        effective = self._defaults | self._existing | self.environment
        self.paths = RuntimePaths(
            project,
            env_file,
            Path(effective.get("SI_RUNTIME_DB", "runtime/si_001.db")).resolve(),
            Path(effective.get("SI_BACKUP_DIR", "backups")).resolve(),
        )
        self.last_result = SetupResult(False, False, "尚未保存。")

    def effective(self, values: Mapping[str, str] | None = None) -> dict[str, str]:
        result = self._defaults | self._existing | dict(values or {}) | self.environment
        for name in ("SI_MEMORY_EMBEDDING_API_KEY", "SI_MEMORY_RERANKER_API_KEY"):
            if name not in result:
                result[name] = result.get("SILICONFLOW_API_KEY", "")
        return result

    def detect(self) -> SetupDetection:
        status = self.env.get_status()
        if status == "Configured" and not self.run_checks().ok:
            status = "Incomplete"
        seed = load_character_seed_data(
            self.paths.project / "data/characters/si_001.yaml"
        )
        message = (
            "已检测到现有应用配置。"
            if status == "Configured"
            else "应用配置：尚未完成。"
        )
        return SetupDetection(
            status,
            f"{seed.identity.working_name} / {seed.identity.development_id}",
            str(seed.identity.internal_id)[:8] + "…",
            message + " 现有角色身份将保持不变。",
        )

    def form_defaults(self) -> dict[str, str]:
        values = self._defaults | self._existing
        names = (
            "SI_MEMORY_EMBEDDING_MODEL",
            "SI_MEMORY_RERANKER_MODEL",
            "SI_ONEBOT_WS_URL",
            "SI_QQ_BOT_USER_ID",
            "SI_QQ_ALLOWED_USER_IDS",
        )
        safe = {name: values.get(name, "") for name in names}
        safe["SI_CHAT_TRANSPORT"] = self._existing.get("SI_CHAT_TRANSPORT", "none")
        return {name: redact(value, self.effective()) for name, value in safe.items()}

    def secret_status(self) -> dict[str, str]:
        return {
            name: "Configured" if self._existing.get(name, "").strip() else "Missing"
            for name in SECRET_FIELDS
        }

    def validate(self, values: Mapping[str, str]) -> None:
        candidate = self._defaults | self._existing | dict(values)
        if candidate.get("SI_CHAT_TRANSPORT") != "none" and any(
            not candidate.get(key, "").strip() for key in SECRET_FIELDS[:2]
        ):
            raise EnvEditError("DeepSeek and SiliconFlow keys are required.")
        if (
            candidate.get("SI_MEMORY_EMBEDDING_PROVIDER") != "api"
            or candidate.get("SI_MEMORY_RERANKER_PROVIDER") != "api"
        ):
            raise EnvEditError("Server setup supports API / API only.")
        if (
            not candidate.get("SI_MEMORY_EMBEDDING_MODEL", "").strip()
            or not candidate.get("SI_MEMORY_RERANKER_MODEL", "").strip()
        ):
            raise EnvEditError("Memory model names are required.")
        transport = candidate.get("SI_CHAT_TRANSPORT")
        if transport not in {"qq", "none"}:
            raise EnvEditError("Select QQ or None.")
        if transport == "qq":
            try:
                _identifier(candidate.get("SI_QQ_BOT_USER_ID", ""), user=True)
                allowed = [
                    part.strip()
                    for part in candidate.get("SI_QQ_ALLOWED_USER_IDS", "").split(",")
                    if part.strip()
                ]
                if not allowed:
                    raise ValueError
                for identifier in allowed:
                    _identifier(identifier, user=True)
                endpoint = urlsplit(candidate.get("SI_ONEBOT_WS_URL", ""))
                if (
                    endpoint.scheme not in {"ws", "wss"}
                    or not endpoint.hostname
                    or endpoint.username
                    or endpoint.password
                    or endpoint.query
                    or endpoint.fragment
                ):
                    raise ValueError
                _ = endpoint.port
            except ValueError:
                raise EnvEditError(
                    "Invalid QQ identifiers/allowlist or WebSocket URL."
                ) from None

    def review(self, values: Mapping[str, str]) -> dict[str, str]:
        candidate = self._defaults | self._existing | dict(values)
        result = {
            "LLM": "DeepSeek API",
            "Embedding": "SiliconFlow API / "
            + candidate.get("SI_MEMORY_EMBEDDING_MODEL", ""),
            "Reranker": "SiliconFlow API / "
            + candidate.get("SI_MEMORY_RERANKER_MODEL", ""),
            "Transport": candidate.get("SI_CHAT_TRANSPORT", "none"),
            "Runtime DB": str(self.paths.database),
        }
        result.update(
            {
                name: "Configured" if candidate.get(name, "").strip() else "Missing"
                for name in SECRET_FIELDS
            }
        )
        return {name: redact(value, candidate) for name, value in result.items()}

    def run_checks(self) -> OperationResult:
        try:
            arguments = [
                sys.executable,
                "-m",
                "evolving_companion.runtime_check",
                "--env-file",
                str(self.env.path),
                "--project-root",
                str(self.paths.project),
            ]
            if self.effective().get("SI_CHAT_TRANSPORT") == "none":
                arguments.append("--offline")
            # Fresh environment avoids stale file-derived keys in a long-running Manager.
            # Credentials are passed only via env, never argv or command output.
            result = subprocess.run(
                arguments,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=45,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            return OperationResult(
                result.returncode == 0,
                check_text(redact(result.stdout, self.effective())).strip()
                or "应用检查失败，请检查本地文件和权限。",
                result.returncode,
            )
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Setup check failed (%s)", type(error).__name__
            )
            return OperationResult(False, "无法执行应用检查；配置仍已保存。")

    def save(self, values: Mapping[str, str]) -> SetupResult:
        try:
            self.validate(values)
            self.env.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = self.env.update(values, expected=self._original)
            self._existing = self.env.read()
            self._original = self.env._text()
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Setup save failed (%s)", type(error).__name__
            )
            self.last_result = SetupResult(
                False,
                False,
                "配置保存失败，请检查字段、权限或是否有并发修改。",
            )
            return self.last_result
        check = self.run_checks()
        no_transport = self._existing.get("SI_CHAT_TRANSPORT") == "none"
        message = "配置已成功保存。" + (" 已备份原有配置。" if backup else "")
        if no_transport:
            message += "\n未配置聊天连接。"
        if any(key in self.environment for key in values):
            message += "\n显式系统环境变量仍优先于配置文件。"
        self.last_result = SetupResult(
            True,
            check.ok and not no_transport,
            message + "\n" + check.message + "\n首次配置 v0.1 不包含网络连通性测试。",
        )
        return self.last_result
