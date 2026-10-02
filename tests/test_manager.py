"""Offline operations and headless keyboard tests; no systemd/root/real data."""

import asyncio
from contextlib import closing
from pathlib import Path
import shutil
import socket
import sqlite3
from subprocess import CompletedProcess
import sys

import httpx
import pytest
from textual.widgets import Button, OptionList, Static

from evolving_companion import manager, manager_services as services
from evolving_companion.deployment import CheckResult, DeploymentPaths
from evolving_companion.manager_app import RestoreConfirmation, SIManagerApp
from evolving_companion.manager_services import (
    DeploymentFacade,
    OperationResult,
    ServiceState,
    ServiceStatus,
    SystemdServiceManager,
    check_summary,
)


class FakeService:
    def __init__(self, state: ServiceState = "Stopped") -> None:
        self.state = state
        self.calls: list[str] = []

    def status(self) -> ServiceStatus:
        self.calls.append("status")
        return ServiceStatus(self.state, OperationResult(True, "inactive", 0))

    def start(self) -> OperationResult:
        self.calls.append("start")
        return OperationResult(False, "permission denied / sudo required", 1)

    def stop(self) -> OperationResult:
        self.calls.append("stop")
        return OperationResult(True, "stop: OK", 0)

    def restart(self) -> OperationResult:
        self.calls.append("restart")
        return OperationResult(True, "restart: OK", 0)

    def recent_logs(self) -> OperationResult:
        self.calls.append("logs")
        return OperationResult(False, "journal permission denied", 1)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("No network or real operating-system command in tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(services.subprocess, "run", forbidden)


@pytest.fixture
def facade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeploymentFacade:
    app = tmp_path / "app"
    (app / "data/characters").mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    shutil.copyfile(source, app / "data/characters/si_001.yaml")
    paths = DeploymentPaths(
        tmp_path,
        app,
        tmp_path / "config/si.env",
        tmp_path / "runtime/db.sqlite",
        tmp_path / "backups",
    )
    paths.database.parent.mkdir()
    environment = {
        "DEEPSEEK_API_KEY": "fake-private-key",
        "SILICONFLOW_API_KEY": "fake-other-key",
        "SI_MEMORY_EMBEDDING_PROVIDER": "api",
        "SI_MEMORY_RERANKER_PROVIDER": "api",
        "SI_MEMORY_EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-8B",
        "SI_MEMORY_RERANKER_MODEL": "Qwen/Qwen3-Reranker-8B",
        "SI_CHAT_TRANSPORT": "qq",
    }
    monkeypatch.setattr(
        services,
        "check_deployment",
        lambda paths, *, health=False: (
            CheckResult("SQLite", not health, "OK" if not health else "MISSING"),
        ),
    )
    return DeploymentFacade(paths, FakeService(), environment)


def test_overview_and_safe_configuration(facade: DeploymentFacade) -> None:
    overview = facade.overview()
    assert overview.character == "玲 / SI-001"
    assert len(overview.internal_identity) == 9
    assert overview.service == "Stopped" and overview.runtime_db == "Missing"
    assert overview.deployment_check == "OK" and overview.health == "Error"
    assert overview.memory_profile == "API / API" and overview.transport == "qq"
    config = facade.configuration()
    assert config["LLM key"] == "Configured"
    assert config["Embedding key"] == "Missing"
    assert "fake-private-key" not in repr(config)
    assert "fake-other-key" not in repr(overview)
    assert not facade.paths.database.exists()


@pytest.mark.parametrize(
    "active, expected",
    [
        ("active", "Running"),
        ("inactive", "Stopped"),
        ("failed", "Failed"),
        ("activating", "Unknown"),
    ],
)
def test_systemd_status_mapping_and_safe_command(
    monkeypatch: pytest.MonkeyPatch, active: str, expected: ServiceState
) -> None:
    calls = []
    monkeypatch.setattr(services.sys, "platform", "linux")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(args, 0, stdout=active, stderr="")

    monkeypatch.setattr(services.subprocess, "run", run)
    assert SystemdServiceManager({}).status().state == expected
    assert calls[0][0] == [
        "systemctl",
        "show",
        "si.service",
        "--property=ActiveState",
        "--value",
    ]
    assert "shell" not in calls[0][1] and calls[0][1]["timeout"] == 15


def test_service_failure_and_logs_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.sys, "platform", "linux")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 1, stdout="", stderr="secret-token")

    monkeypatch.setattr(services.subprocess, "run", run)
    service = SystemdServiceManager({"API_KEY": "secret-token"})
    result = service.start()
    assert not result.ok and result.exit_code == 1
    assert "sudo required" in result.message and "secret-token" not in result.message
    assert calls[-1] == ["systemctl", "--no-ask-password", "start", "si.service"]
    assert not service.recent_logs().ok
    monkeypatch.setattr(
        services.subprocess,
        "run",
        lambda args, **kwargs: CompletedProcess(
            args,
            0,
            stdout="known=secret-token\nAuthorization: Bearer unknown\nTOKEN=unknown\x1b[31m",
            stderr="",
        ),
    )
    logs = service.recent_logs()
    assert (
        logs.ok and "secret-token" not in logs.message and "unknown" not in logs.message
    )
    assert "\x1b" not in logs.message


def test_non_linux_does_not_execute_or_read_deployment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["si"])
    monkeypatch.setattr(
        manager, "load_server_env", lambda path: pytest.fail("No /opt/si read")
    )
    assert manager.main() == 0
    assert "Linux deployment" in capsys.readouterr().out
    assert not SystemdServiceManager({}).stop().ok


def test_command_unavailable_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.sys, "platform", "linux")

    def missing(*args, **kwargs):
        raise FileNotFoundError("fake-private-key")

    monkeypatch.setattr(services.subprocess, "run", missing)
    result = SystemdServiceManager({}).recent_logs()
    assert not result.ok and "FileNotFoundError" in result.message
    assert "fake-private-key" not in result.message


def test_check_mapping_and_existing_api_reuse(facade: DeploymentFacade) -> None:
    assert check_summary(facade.checks()) == "OK"
    assert check_summary(facade.checks(health=True)) == "Error"
    assert check_summary(()) == "Error"


def test_backup_listing_and_service_reuse(
    facade: DeploymentFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert facade.backups() == ()
    calls = []
    facade.paths.backups.mkdir()
    backup = facade.paths.backups / "si_001_20261002_120000.db"
    backup.write_bytes(b"fixture")
    (facade.paths.backups / "unrelated.db").write_bytes(b"ignored")
    monkeypatch.setattr(
        services,
        "backup_database",
        lambda database, directory: calls.append((database, directory)) or backup,
    )
    assert facade.create_backup().ok
    assert calls == [(facade.paths.database, facade.paths.backups)]
    records = facade.backups()
    assert len(records) == 1 and records[0].name == backup.name
    assert records[0].size == 7 and "+00:00" in records[0].created_at


def test_restore_confirmation_and_stopped_contract(
    facade: DeploymentFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    facade.paths.backups.mkdir()
    name = "si_001_20261002_120000.db"
    (facade.paths.backups / name).write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(
        services,
        "restore_database",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Path("pre_restore.db"),
    )
    assert not facade.restore(name).ok and calls == []
    assert not facade.restore("../elsewhere.db", confirmed=True).ok and calls == []
    assert facade.restore(name, confirmed=True).ok
    assert calls[-1][1] == {"service_stopped": True}
    facade.service = FakeService("Running")
    facade.restore(name, confirmed=True)
    assert calls[-1][1] == {"service_stopped": False}


def test_real_backup_restore_through_facade(facade: DeploymentFacade) -> None:
    with closing(sqlite3.connect(facade.paths.database)) as connection, connection:
        connection.execute("CREATE TABLE payload(value TEXT)")
        connection.execute("INSERT INTO payload VALUES ('old')")
    assert facade.create_backup().ok
    name = facade.backups()[0].name
    with closing(sqlite3.connect(facade.paths.database)) as connection, connection:
        connection.execute("UPDATE payload SET value='new'")
    assert facade.restore(name, confirmed=True).ok
    assert any(item.name.startswith("pre_restore_") for item in facade.backups())
    with closing(sqlite3.connect(facade.paths.database)) as connection:
        assert connection.execute("SELECT value FROM payload").fetchone() == ("old",)


def test_help_without_deployment_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["si", "--help"])
    with pytest.raises(SystemExit) as error:
        manager.main()
    assert error.value.code == 0


def test_headless_navigation_clean_exit_and_restore_failure(
    facade: DeploymentFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    facade.paths.backups.mkdir()
    name = "si_001_20261002_120000.db"
    (facade.paths.backups / name).write_bytes(b"invalid SQLite")
    app = SIManagerApp(facade)

    async def smoke() -> None:
        async with app.run_test(size=(110, 38)) as pilot:
            await app.workers.wait_for_complete()
            assert "Stopped" in str(app.query_one("#output", Static).content)
            # Arrow/Enter menu navigation, refresh, Esc back.
            await pilot.press("down", "enter")
            await app.workers.wait_for_complete()
            assert app.page == "Service"
            await pilot.click("#start")
            await app.workers.wait_for_complete()
            assert "sudo required" in str(app.query_one("#result", Static).content)
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert app.page == "Overview"
            nav = app.query_one("#navigation", OptionList)
            nav.highlighted = 5
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            app.query_one("#backups", OptionList).highlighted = 0
            await pilot.click("#restore")
            assert isinstance(app.screen, RestoreConfirmation)
            assert app.screen.focused is app.screen.query_one("#cancel", Button)
            await pilot.press("r")
            assert isinstance(app.screen, RestoreConfirmation)
            await pilot.press("escape")
            assert not isinstance(app.screen, RestoreConfirmation)
            assert app.page == "Backup / Restore"
            # Textual suppresses rapid re-presses during Button's active animation.
            await pilot.pause(0.35)
            app.query_one("#restore", Button).focus()
            await pilot.press("enter")
            assert isinstance(app.screen, RestoreConfirmation)
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()
            assert "ValueError" in str(app.query_one("#result", Static).content)
            assert not app.busy
            await pilot.press("q")
            assert not app.is_running

    asyncio.run(smoke())


def test_facade_real_checks_are_offline(
    facade: DeploymentFacade, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evolving_companion.deployment import check_deployment

    monkeypatch.setattr(services, "check_deployment", check_deployment)
    # Required files/config are missing by design; diagnostic must not need a network.
    assert check_summary(facade.checks()) == "Error"
    assert check_summary(facade.checks(health=True)) == "Error"
    assert not facade.paths.database.exists()


def test_logs_error_and_missing_config_do_not_crash(facade: DeploymentFacade) -> None:
    app = SIManagerApp(facade)

    async def smoke() -> None:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#navigation", OptionList).highlighted = 6
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert "journal permission denied" in str(
                app.query_one("#output", Static).content
            )
            (facade.paths.app / "data/characters/si_001.yaml").unlink()
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert "FileNotFoundError" in str(app.query_one("#result", Static).content)
            await pilot.press("q")

    asyncio.run(smoke())
