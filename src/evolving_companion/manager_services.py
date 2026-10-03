"""Fresh configuration snapshots and local checks; never initialize Core."""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.config_env import ApplicationEnvService, OperationResult, redact
from evolving_companion.local_env import PROJECT_ROOT
from evolving_companion.runtime_config import RuntimePaths, check_sqlite
from evolving_companion.runtime_control import (
    NativeProcessController,
    RuntimeController,
)
from evolving_companion.setup_services import SetupService
from evolving_companion.ui_text import check_text


@dataclass(frozen=True)
class ManagerStatus:
    character: str
    internal_identity: str
    runtime: str
    configuration: str
    runtime_db: str
    transport: str
    health: str
    llm: str = "未测试"
    memory: str = "未测试"


class ManagerService:
    def __init__(
        self,
        env_file: Path,
        *,
        project_root: Path | None = None,
        environment: Mapping[str, str] | None = None,
        controller: RuntimeController | None = None,
    ) -> None:
        self.project = (project_root or PROJECT_ROOT).resolve()
        self.env_file = env_file.resolve()
        self.environment = dict(os.environ if environment is None else environment)
        self.controller = controller
        self._native: NativeProcessController | None = None
        self._web = None

    def effective(self) -> dict[str, str]:
        service = ApplicationEnvService(
            self.env_file, self.project / "config/si.env.example"
        )
        values = service.read() | self.environment
        for name in ("SI_MEMORY_EMBEDDING_API_KEY", "SI_MEMORY_RERANKER_API_KEY"):
            if name not in values:
                values[name] = values.get("SILICONFLOW_API_KEY", "")
        return values

    def paths(self) -> RuntimePaths:
        values = self.effective()

        def path(name: str, default: str) -> Path:
            candidate = Path(values.get(name, default))
            return (
                candidate if candidate.is_absolute() else self.project / candidate
            ).resolve()

        return RuntimePaths(
            self.project,
            self.env_file,
            path("SI_RUNTIME_DB", "runtime/si_001.db"),
            path("SI_BACKUP_DIR", "backups"),
        )

    def backend(self) -> RuntimeController:
        # File config is re-read, but child receives only the original OS environment.
        # server.py is the single authoritative env-file loader for the actual runtime.
        if self.controller is not None:
            return self.controller
        paths = self.paths()
        if self._native is None or self._native.paths != paths:
            self._native = NativeProcessController(paths, self.environment)
        return self._native

    def check(self, *, health: bool = False) -> OperationResult:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "evolving_companion.health"
                    if health
                    else "evolving_companion.runtime_check",
                    "--env-file",
                    str(self.env_file),
                    "--project-root",
                    str(self.project),
                ],
                cwd=self.project,
                env=self.environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            return OperationResult(
                result.returncode == 0,
                check_text(redact(result.stdout, self.effective())),
                result.returncode,
            )
        except Exception as error:
            return OperationResult(
                False, f"无法执行本地检查（{type(error).__name__}）。"
            )

    def status(self) -> ManagerStatus:
        values = self.effective()
        try:
            seed = load_character_seed_data(
                self.project / "data/characters/si_001.yaml"
            )
            character = f"{seed.identity.working_name} / {seed.identity.development_id}"
            identity = str(seed.identity.internal_id)[:8] + "…"
        except Exception:
            character, identity = "Unavailable", "Unavailable"
        database = self.paths().database
        db_status = "Missing"
        if database.exists():
            try:
                check_sqlite(database)
                db_status = "Available"
            except Exception:
                db_status = "Error"
        check = self.check()
        config = (
            "OK"
            if check.ok
            else ("Incomplete" if not self.env_file.exists() else "Error")
        )
        health = self.check(health=True).ok
        mode = values.get("SI_CHAT_TRANSPORT", "Missing")
        transport = "qq — SnowLuma / OneBot" if mode == "qq" else mode
        return ManagerStatus(
            redact(character, values),
            identity,
            self.backend().status().state,
            config,
            db_status,
            redact(transport, values),
            "OK (local only)" if health else "Error (local only)",
            "已配置；API 未测试" if values.get("DEEPSEEK_API_KEY") else "缺少密钥",
            "密钥已配置；连接未测试"
            if values.get("SILICONFLOW_API_KEY")
            else "缺少密钥",
        )

    def start(self) -> OperationResult:
        check = self.check()
        if not check.ok:
            return OperationResult(
                False,
                "无法启动：运行配置检查未通过，请打开“配置”。\n" + check.message,
            )
        return self.backend().start()

    def stop(self) -> OperationResult:
        return self.backend().stop()

    def restart(self) -> OperationResult:
        if not self.check().ok:
            return OperationResult(
                False,
                "无法重启：配置检查未通过；现有运行进程保持不变。",
            )
        return self.backend().restart()

    def logs(self) -> OperationResult:
        result = self.backend().logs()
        return OperationResult(
            result.ok, redact(result.message, self.effective()), result.exit_code
        )

    def setup_service(self) -> SetupService:
        return SetupService(
            self.env_file, environment=self.environment, project_root=self.project
        )

    def web_setup(self) -> str:
        from evolving_companion.web_setup import WebSetupServer
        from evolving_companion.web_setup_services import WebSetupService

        if self._web is not None and not self._web.is_running:
            self.close_web_setup()
        if self._web is None:
            server = WebSetupServer(
                WebSetupService(self.env_file, self.project, self.environment)
            )
            server.start()
            self._web = server
        return (
            "Web 配置已启动（仅本机；访问地址含临时凭证，请勿分享）\n"
            + self._web.url
            + f"\n\n远程 SSH：在自己电脑执行（替换 user@server）：\nssh -N -L {self._web.port}:127.0.0.1:{self._web.port} user@server\n"
            + "保持 SSH 窗口开启，再在自己电脑浏览器打开以上地址。\n"
            + "保存后返回状态并启动/重启。si setup 仍可使用终端配置。\n"
            + "按 s 停止 Web 配置；退出管理器也会关闭 Web，但不会停止 Core。"
        )

    def close_web_setup(self) -> None:
        if self._web is not None:
            self._web.stop()
            self._web = None
