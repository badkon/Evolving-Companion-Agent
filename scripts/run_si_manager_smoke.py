"""Fake/headless Manager smoke: no processes, official DB, keys or network."""

import asyncio
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from unittest.mock import patch

from textual.widgets import OptionList, Static

from evolving_companion.config_env import OperationResult
from evolving_companion.manager_app import PAGES, SIManagerApp
from evolving_companion.manager_services import ManagerService
from evolving_companion.runtime_control import RuntimeState, RuntimeStatus


class FakeController:
    def __init__(self) -> None:
        self.state: RuntimeState = "Stopped"
        self.calls: list[str] = []

    def start(self) -> OperationResult:
        self.state = "Running"
        self.calls.append("start")
        return OperationResult(True, "模拟启动成功")

    def stop(self) -> OperationResult:
        self.state = "Stopped"
        self.calls.append("stop")
        return OperationResult(True, "模拟停止成功")

    def restart(self) -> OperationResult:
        self.state = "Running"
        self.calls.append("restart")
        return OperationResult(True, "模拟重启成功")

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(self.state, "Fake process only")

    def logs(self) -> OperationResult:
        return OperationResult(True, "模拟日志；没有真实运行进程")


async def smoke(root: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    for directory in ("config", "data/characters", "runtime"):
        (root / directory).mkdir(parents=True)
    for name in ("config/si.env.example", "data/characters/si_001.yaml"):
        shutil.copyfile(repo / name, root / name)
    seed = root / "data/characters/si_001.yaml"
    before = seed.read_bytes()
    controller = FakeController()
    service = ManagerService(
        root / "config/si.env", project_root=root, environment={}, controller=controller
    )
    app = SIManagerApp(service)
    with (
        patch.object(service, "web_setup", return_value="Web 配置已启动（fake）"),
        patch.object(
            service, "check", return_value=OperationResult(True, "Fake local check")
        ),
    ):
        async with app.run_test(size=(100, 40)) as pilot:
            await app.workers.wait_for_complete()
            assert "状态" in str(app.query_one("#output", Static).content)
            for choice in ("Start", "Logs", "Configure"):
                app.query_one("#navigation", OptionList).highlighted = PAGES.index(
                    choice
                )
                await pilot.press("enter")
                await app.workers.wait_for_complete()
            assert app.page == "Configure"
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert app.page == "Status"
            await pilot.press("q")
            await app.workers.wait_for_complete()
            assert not app.is_running
    assert controller.calls == ["start"] and controller.state == "Running"
    assert not service.paths().database.exists() and not service.env_file.exists()
    assert seed.read_bytes() == before
    print("PASS: Status -> Start -> Logs -> Web Setup (fake) -> Status -> q.")
    print(
        "Fake runtime remains Running after Manager exit; no Core, DB, process or network used."
    )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="si-manager-smoke-") as directory:
        with (
            patch("socket.create_connection", side_effect=AssertionError("No network")),
            patch("subprocess.run", side_effect=AssertionError("No real checks")),
            patch("subprocess.Popen", side_effect=AssertionError("No real processes")),
        ):
            asyncio.run(smoke(Path(directory)))
