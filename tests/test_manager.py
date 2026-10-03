"""Headless Manager tests: synthetic config, no API, transport or Core startup."""

import asyncio
import os
from pathlib import Path
import shutil
import socket
from subprocess import CompletedProcess
import sys
import tomllib

import httpx
import pytest
from textual.widgets import OptionList, Static

from evolving_companion import manager, manager_services, setup
from evolving_companion.config_env import OperationResult
from evolving_companion.manager_app import PAGES, SIManagerApp
from evolving_companion.manager_services import ManagerService
from evolving_companion.runtime_control import RuntimeState, RuntimeStatus
from evolving_companion.setup_app import SetupScreen


class FakeController:
    def __init__(self):
        self.state: RuntimeState = "Stopped"
        self.calls: list[str] = []

    def start(self) -> OperationResult:
        self.calls.append("start")
        self.state = "Running"
        return OperationResult(True, "Fake runtime started")

    def stop(self) -> OperationResult:
        self.calls.append("stop")
        self.state = "Stopped"
        return OperationResult(True, "Fake runtime stopped")

    def restart(self) -> OperationResult:
        self.calls.append("restart")
        self.state = "Running"
        return OperationResult(True, "Fake runtime restarted")

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(self.state, "Fake only")

    def logs(self) -> OperationResult:
        return OperationResult(True, "[bold] fake-key \x1b\x00 log")


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No network, runtime or transport in Manager tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(manager_services.subprocess, "run", forbidden)
    monkeypatch.setattr(manager_services.subprocess, "Popen", forbidden)


@pytest.fixture
def service(tmp_path: Path, monkeypatch):
    for directory in ("config", "runtime", "data/characters", "data/worlds"):
        (tmp_path / directory).mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    for name in (
        "config/si.env.example",
        "data/characters/si_001.yaml",
        "data/worlds/si_world.yaml",
    ):
        source = repo / name
        if source.exists():
            shutil.copyfile(source, tmp_path / name)
    result = ManagerService(
        tmp_path / "config/si.env",
        project_root=tmp_path,
        environment={},
        controller=FakeController(),
    )
    monkeypatch.setattr(
        result, "check", lambda **kwargs: OperationResult(True, "Fake local check")
    )
    return result


def test_cli_dispatch_and_help(service, monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager_services,
        "ManagerService",
        lambda *a, **k: calls.append((a, k)) or service,
    )
    monkeypatch.setattr(SIManagerApp, "run", lambda self: calls.append("manager"))
    assert manager.main(["--env-file", str(service.env_file)]) == 0
    assert calls[-1] == "manager" and calls[0][0] == (service.env_file,)
    monkeypatch.setattr(setup, "main", lambda args: calls.append(args) or 0)
    assert (
        manager.main(
            [
                "setup",
                "--env-file",
                str(service.env_file),
                "--project-root",
                str(service.project),
            ]
        )
        == 0
    )
    assert calls[-1] == [
        "--env-file",
        str(service.env_file),
        "--project-root",
        str(service.project),
    ]
    with pytest.raises(SystemExit) as result:
        manager.main(["--help"])
    assert result.value.code == 0
    repo = Path(__file__).resolve().parents[1]
    assert (
        tomllib.loads((repo / "pyproject.toml").read_text())["project"]["scripts"]["si"]
        == "evolving_companion.manager:main"
    )


def test_fresh_file_environment_and_original_os_priority(service, monkeypatch):
    original = dict(os.environ)
    service.environment = {"SILICONFLOW_API_KEY": "fake-os-key"}
    service.env_file.write_text(
        "DEEPSEEK_API_KEY=fake-first\nSILICONFLOW_API_KEY=fake-file-key\nSI_CHAT_TRANSPORT=none\n"
    )
    assert service.effective()["SILICONFLOW_API_KEY"] == "fake-os-key"
    assert service.effective()["SI_MEMORY_EMBEDDING_API_KEY"] == "fake-os-key"
    service.env_file.write_text("SI_CHAT_TRANSPORT=qq\nDEEPSEEK_API_KEY=fake-second\n")
    assert service.effective()["DEEPSEEK_API_KEY"] == "fake-second"
    assert service.effective()["SI_CHAT_TRANSPORT"] == "qq"
    service.env_file.write_text("SI_CHAT_TRANSPORT=none\n")
    assert "DEEPSEEK_API_KEY" not in service.effective()
    assert dict(os.environ) == original
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return CompletedProcess(
            command, 0, "fake-os-key fake-current-key\x1b", "private error"
        )

    service.env_file.write_text("DEEPSEEK_API_KEY=fake-current-key\n")
    monkeypatch.setattr(manager_services.subprocess, "run", run)
    # Restore the real check, not the fixture's fake.
    result = ManagerService.check(service)
    assert (
        result.ok
        and "fake-os-key" not in result.message
        and "fake-current-key" not in result.message
    )
    command, options = calls[0]
    assert command == [
        sys.executable,
        "-m",
        "evolving_companion.runtime_check",
        "--env-file",
        str(service.env_file),
        "--project-root",
        str(service.project),
    ]
    assert options["env"] == service.environment and options["stdin"] == -3
    assert options["cwd"] == service.project and "shell" not in options
    assert dict(os.environ) == original


@pytest.mark.parametrize("mode", ["none", "qq"])
def test_status_none_qq_and_no_db_initialization(service, mode):
    service.env_file.write_text(
        f"SI_CHAT_TRANSPORT={mode}\nDEEPSEEK_API_KEY=fake-key\n"
    )
    before = (service.project / "data/characters/si_001.yaml").read_bytes()
    status = service.status()
    assert "玲" in status.character and len(status.internal_identity) == 9
    assert status.configuration == "OK" and status.runtime == "Stopped"
    assert status.runtime_db == "Missing" and "local only" in status.health
    assert status.transport == ("none" if mode == "none" else "qq — SnowLuma / OneBot")
    assert service.start().ok  # Does not consult Setup's historical qq-only status.
    assert not service.paths().database.exists()
    assert before == (service.project / "data/characters/si_001.yaml").read_bytes()
    assert "fake-key" not in service.logs().message


def test_missing_invalid_config_and_stop_not_gated(service, monkeypatch):
    monkeypatch.setattr(
        service, "check", lambda **kwargs: OperationResult(False, "Failed check")
    )
    assert service.status().configuration == "Incomplete"
    assert not service.start().ok and not service.restart().ok
    assert service.stop().ok
    service.env_file.write_text("SI_CHAT_TRANSPORT=none\n")
    assert service.status().configuration == "Error"
    assert isinstance(service.controller, FakeController)
    assert service.controller.calls == ["stop"]


def test_native_backend_reuses_child_but_refreshes_paths(service):
    service.controller = None
    first = service.backend()
    assert service.backend() is first
    service.env_file.write_text("SI_RUNTIME_DB=runtime/other.db\n")
    assert service.backend() is not first
    assert service.paths().database == service.project / "runtime/other.db"


def test_actual_local_validation_accepts_none_without_transport(service, monkeypatch):
    from evolving_companion.runtime_config import check_runtime, load_runtime_env

    template = (service.project / "config/si.env.example").read_text()
    service.env_file.write_text(
        template
        + "\nSI_CHAT_TRANSPORT=none\nDEEPSEEK_API_KEY=fake-ds\nSILICONFLOW_API_KEY=fake-sf\n"
    )
    # Exercise the same validation locally with a fresh temporary environment.
    with monkeypatch.context() as isolated:
        for key in tuple(os.environ):
            isolated.delenv(key)
        load_runtime_env(service.env_file)
        results = check_runtime(service.paths())
        assert all(item.ok for item in results), results
        assert not service.paths().database.exists()
        assert (
            not any(name in os.environ for name in ("SI_QQ_ALLOWED_USER_IDS",))
            or not os.environ["SI_QQ_ALLOWED_USER_IDS"]
        )


def test_headless_navigation_lifecycle_configure_and_exit(service):
    app = SIManagerApp(service)

    async def run():
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            output = app.query_one("#output", Static)
            assert "Status" in str(output.content) and not output._render_markup
            for choice in ("Start", "Stop", "Restart", "Logs"):
                app.query_one("#navigation", OptionList).highlighted = PAGES.index(
                    choice
                )
                await pilot.press("enter")
                await app.workers.wait_for_complete()
            assert "[bold]" in str(output.content) and "\x1b" not in str(output.content)
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert app.page == "Status"
            app.query_one("#navigation", OptionList).highlighted = PAGES.index(
                "Configure"
            )
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, SetupScreen)
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert not isinstance(app.screen, SetupScreen) and app.page == "Status"
            await pilot.press("q")
            assert not app.is_running

    asyncio.run(run())
    assert isinstance(service.controller, FakeController)
    assert service.controller.calls == ["start", "stop", "restart"]
    assert service.controller.state == "Running"  # q did not stop child.
    assert not service.paths().database.exists()


def test_busy_guard_and_safe_error_rendering(service, caplog):
    app = SIManagerApp(service)

    def fail():
        raise ValueError("fake-private-secret")

    async def run():
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.busy = True
            app.perform(service.start)
            await app.workers.wait_for_complete()
            await pilot.press("q")
            assert app.is_running
            app.busy = False
            app.perform(fail)
            await app.workers.wait_for_complete()
            assert "ValueError" in str(app.query_one("#result", Static).content)
            assert "fake-private-secret" not in str(
                app.query_one("#result", Static).content
            )
            await pilot.press("q")

    asyncio.run(run())
    assert "fake-private-secret" not in caplog.text
    assert (
        isinstance(service.controller, FakeController) and not service.controller.calls
    )
