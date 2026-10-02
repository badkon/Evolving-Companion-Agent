"""Local operations facade. No Character Core or third-party network calls."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
import logging
import os
import re
import subprocess
import sys
from typing import Literal, Protocol

from evolving_companion.backup import backup_database
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.deployment import CheckResult, DeploymentPaths, check_deployment
from evolving_companion.restore import restore_database

ServiceState = Literal["Running", "Stopped", "Failed", "Unknown"]
ServiceAction = Literal["start", "stop", "restart"]


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    message: str
    exit_code: int | None = None


@dataclass(frozen=True)
class ServiceStatus:
    state: ServiceState
    result: OperationResult


class ServiceManager(Protocol):
    def status(self) -> ServiceStatus: ...
    def start(self) -> OperationResult: ...
    def stop(self) -> OperationResult: ...
    def restart(self) -> OperationResult: ...
    def recent_logs(self) -> OperationResult: ...


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
    # Journal/config values must never inject terminal control sequences.
    return "".join(c for c in text if c in "\n\t" or (c.isprintable() and c != "\x1b"))


class SystemdServiceManager:
    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.environment = os.environ if environment is None else environment

    def _command(self, arguments: list[str]) -> OperationResult:
        if sys.platform != "linux":
            return OperationResult(
                False, "SI Manager is intended for Linux deployment."
            )
        try:
            result = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as error:
            logging.getLogger(__name__).warning(
                "Manager command failed (%s)", type(error).__name__
            )
            return OperationResult(
                False, f"Command unavailable/failed ({type(error).__name__})"
            )
        if result.returncode:
            # Never return raw stderr: it can contain private environment/input.
            return OperationResult(
                False,
                "Command failed: permission denied / sudo required, or service unavailable. "
                "请在受信任终端检查 systemd/journal 权限。",
                result.returncode,
            )
        return OperationResult(True, redact(result.stdout.strip(), self.environment), 0)

    def status(self) -> ServiceStatus:
        result = self._command(
            ["systemctl", "show", "si.service", "--property=ActiveState", "--value"]
        )
        states: dict[str, ServiceState] = {
            "active": "Running",
            "inactive": "Stopped",
            "failed": "Failed",
        }
        state = states.get(result.message, "Unknown")
        return ServiceStatus(state, result)

    def _action(self, action: ServiceAction) -> OperationResult:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Unsupported service action")
        result = self._command(["systemctl", "--no-ask-password", action, "si.service"])
        return OperationResult(
            result.ok,
            f"{action}: OK" if result.ok else result.message,
            result.exit_code,
        )

    def start(self) -> OperationResult:
        return self._action("start")

    def stop(self) -> OperationResult:
        return self._action("stop")

    def restart(self) -> OperationResult:
        return self._action("restart")

    def recent_logs(self) -> OperationResult:
        return self._command(
            [
                "journalctl",
                "-u",
                "si.service",
                "--no-pager",
                "--lines=100",
                "--output=short",
            ]
        )


@dataclass(frozen=True)
class Overview:
    character: str
    internal_identity: str
    service: ServiceState
    runtime_db: str
    deployment_check: str
    health: str
    memory_profile: str
    transport: str
    app_version: str
    setup_status: str = "Setup Required"


@dataclass(frozen=True)
class BackupInfo:
    name: str
    created_at: str
    size: int


def check_summary(results: tuple[CheckResult, ...]) -> str:
    return "OK" if results and all(item.ok for item in results) else "Error"


class DeploymentFacade:
    def __init__(
        self,
        paths: DeploymentPaths,
        service: ServiceManager,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.paths, self.service = paths, service
        self.environment = os.environ if environment is None else environment

    def checks(self, *, health: bool = False) -> tuple[CheckResult, ...]:
        return check_deployment(self.paths, health=health)

    def configuration(self) -> dict[str, str]:
        # Only this allowlist reaches UI models. No whole env/file dump.
        fields = {
            "LLM provider": "DeepSeek API",
            "Runtime DB": str(self.paths.database),
            "Secrets file": str(self.paths.env_file),
        }
        for label, name in (
            ("Embedding provider", "SI_MEMORY_EMBEDDING_PROVIDER"),
            ("Embedding model", "SI_MEMORY_EMBEDDING_MODEL"),
            ("Reranker provider", "SI_MEMORY_RERANKER_PROVIDER"),
            ("Reranker model", "SI_MEMORY_RERANKER_MODEL"),
            ("Transport", "SI_CHAT_TRANSPORT"),
        ):
            fields[label] = self.environment.get(name, "Missing")
        for label, name in (
            ("LLM key", "DEEPSEEK_API_KEY"),
            ("SiliconFlow key", "SILICONFLOW_API_KEY"),
            ("Embedding key", "SI_MEMORY_EMBEDDING_API_KEY"),
            ("Reranker key", "SI_MEMORY_RERANKER_API_KEY"),
        ):
            fields[label] = (
                "Configured" if self.environment.get(name, "").strip() else "Missing"
            )
        return {
            label: redact(value, self.environment) for label, value in fields.items()
        }

    def overview(self) -> Overview:
        seed = load_character_seed_data(self.paths.app / "data/characters/si_001.yaml")
        config = self.configuration()
        deployment_status = check_summary(self.checks())
        return Overview(
            redact(
                f"{seed.identity.working_name} / {seed.identity.development_id}",
                self.environment,
            ),
            str(seed.identity.internal_id)[:8] + "…",
            self.service.status().state,
            "Available" if self.paths.database.is_file() else "Missing",
            deployment_status,
            check_summary(self.checks(health=True)),
            f"{config['Embedding provider'].upper()} / {config['Reranker provider'].upper()}",
            config["Transport"],
            version("evolving-companion-agent"),
            "Ready"
            if self.paths.env_file.is_file() and deployment_status == "OK"
            else "Setup Required",
        )

    def backups(self) -> tuple[BackupInfo, ...]:
        if not self.paths.backups.exists():
            return ()
        records = []
        for path in self.paths.backups.glob("*.db"):
            if path.is_symlink() or not re.fullmatch(
                r"(?:si_001|pre_restore)_\d{8}_\d{6}\.db", path.name
            ):
                continue
            info = path.stat()
            records.append(
                BackupInfo(
                    path.name,
                    datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                    info.st_size,
                )
            )
        return tuple(
            sorted(records, key=lambda record: record.created_at, reverse=True)
        )

    def create_backup(self) -> OperationResult:
        path = backup_database(self.paths.database, self.paths.backups)
        return OperationResult(True, f"Backup created: {path.name}")

    def restore(self, name: str, *, confirmed: bool = False) -> OperationResult:
        if not confirmed:
            return OperationResult(False, "Restore requires explicit confirmation.")
        if name not in {record.name for record in self.backups()}:
            return OperationResult(False, "Select an existing local backup.")
        path = (self.paths.backups / name).resolve()
        if path.parent != self.paths.backups.resolve():
            return OperationResult(False, "Invalid backup path.")
        status = self.service.status()
        pre_restore = restore_database(
            path,
            self.paths.database,
            self.paths.backups,
            service_stopped=status.result.ok and status.state in {"Stopped", "Failed"},
        )
        return OperationResult(
            True, f"Restore completed; previous DB preserved: {pre_restore.name}"
        )
