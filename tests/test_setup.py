"""First-run setup tests use fake secrets, temporary paths, and no real systemd."""

import asyncio
from pathlib import Path
import shutil
import socket
from subprocess import CompletedProcess
import sys
import traceback

import httpx
import pytest
from textual.widgets import Button, Input, Select, Static

from evolving_companion import deployment_env, manager, setup_services
from evolving_companion.deployment_env import (
    DeploymentEnvService,
    EnvEditError,
    parse_env,
)
from evolving_companion.manager_app import SIManagerApp
from evolving_companion.manager_services import (
    DeploymentFacade,
    OperationResult,
    ServiceStatus,
)
from evolving_companion.setup_app import SetupApp, SetupScreen
from evolving_companion.setup_services import SetupService

KEYS = {
    "DEEPSEEK_API_KEY": "fake-deepseek-secret",
    "SILICONFLOW_API_KEY": "fake-silicon-secret",
    "SI_ONEBOT_ACCESS_TOKEN": "fake-onebot-token",
}
REAL_PROTECT_FILE = deployment_env.protect_file


class FakeSystemd:
    def __init__(self) -> None:
        self.starts = 0

    def status(self) -> ServiceStatus:
        return ServiceStatus("Stopped", OperationResult(True, "inactive", 0))

    def start(self) -> OperationResult:
        self.starts += 1
        return OperationResult(False, "permission denied / sudo required", 1)

    def stop(self) -> OperationResult:
        return OperationResult(True, "stopped", 0)

    def restart(self) -> OperationResult:
        return OperationResult(True, "restarted", 0)

    def recent_logs(self) -> OperationResult:
        return OperationResult(True, "fake journal", 0)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("No real network or systemd in setup tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(setup_services.subprocess, "run", forbidden)
    # No chmod/chown/Linux users in Windows/CI fixtures.
    monkeypatch.setattr(deployment_env, "protect_file", lambda path: None)


@pytest.fixture
def setup(tmp_path: Path) -> SetupService:
    root = tmp_path / "si"
    for directory in ("app/deploy", "app/data/characters", "config", "runtime"):
        (root / directory).mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    template = (repo / "deploy/si.env.example").read_text(encoding="utf-8")
    (root / "app/deploy/si.env.example").write_text(
        template.replace("/opt/si", root.as_posix()), encoding="utf-8"
    )
    shutil.copyfile(
        repo / "data/characters/si_001.yaml", root / "app/data/characters/si_001.yaml"
    )
    return SetupService(
        root / "config/si.env", FakeSystemd(), {"SI_DEPLOY_ROOT": str(root)}
    )


def updates() -> dict[str, str]:
    return KEYS | {
        "SI_CHAT_TRANSPORT": "qq",
        "SI_QQ_BOT_USER_ID": "202",
        "SI_QQ_ALLOWED_USER_IDS": "101",
    }


def fake_check(
    monkeypatch: pytest.MonkeyPatch, setup: SetupService, *, ok: bool = True
) -> list[str]:
    calls: list[str] = []

    def check() -> OperationResult:
        calls.append("deploy_check")
        return OperationResult(
            ok, "SQLite OK" if ok else "Transport ERROR", 0 if ok else 1
        )

    monkeypatch.setattr(setup, "run_checks", check)
    return calls


def test_missing_incomplete_configured_detection(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert setup.detect().status == "Missing"
    setup.env.path.write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    assert setup.detect().status == "Incomplete"
    setup = SetupService(setup.env.path, setup.service, setup.environment)
    fake_check(monkeypatch, setup)
    assert setup.save(updates()).saved
    detection = setup.detect()
    assert detection.status == "Configured"
    assert "Existing deployment configuration detected" in detection.message
    assert "will be preserved" in detection.message
    assert len(detection.internal_identity) == 9


def test_keep_replace_secrets_and_safe_models(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_check(monkeypatch, setup)
    assert setup.save(updates()).saved
    assert set(setup.secret_status().values()) == {"Configured"}
    # Keep is represented by absent fields; replacement only changes explicitly supplied key.
    assert setup.save({"SI_MEMORY_EMBEDDING_MODEL": "other-model"}).saved
    assert setup.env.read()["DEEPSEEK_API_KEY"] == KEYS["DEEPSEEK_API_KEY"]
    # Avoid same-second backup collision without adding sleep/retry to production.
    for path in setup.env.path.parent.glob("si.env.bak.*"):
        path.rename(path.with_suffix(path.suffix + ".previous"))
    assert setup.save({"DEEPSEEK_API_KEY": "fake-replacement"}).saved
    assert setup.env.read()["DEEPSEEK_API_KEY"] == "fake-replacement"
    for model in (
        setup.detect(),
        setup.secret_status(),
        setup.review({}),
        setup.form_defaults(),
        setup.last_result,
    ):
        assert "fake-replacement" not in repr(model)
        assert all(value not in repr(model) for value in KEYS.values())


def test_unknown_fields_comments_quotes_backup_atomic_permissions(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = "# custom comment\nUNKNOWN='keep # exactly' # suffix\nEMPTY=\nDEEPSEEK_API_KEY=\n"
    setup.env.path.write_text(original, encoding="utf-8")
    protection: list[Path] = []
    monkeypatch.setattr(deployment_env, "protect_file", protection.append)
    replacements = []
    replace = deployment_env.os.replace

    def atomic(source, target):
        replacements.append((source, target))
        assert Path(source).parent == Path(target).parent
        assert Path(source) in protection
        assert Path(target).read_text(encoding="utf-8") == original
        replace(source, target)

    monkeypatch.setattr(deployment_env.os, "replace", atomic)
    secret = 'fake "quoted" \\ token # literal ${UNCHANGED}'
    backup = setup.env.update({"DEEPSEEK_API_KEY": secret}, expected=original)
    assert backup is not None and backup in protection
    assert backup.read_text(encoding="utf-8") == original
    result = setup.env.path.read_text(encoding="utf-8")
    assert (
        "# custom comment" in result and "UNKNOWN='keep # exactly' # suffix" in result
    )
    assert setup.env.read()["DEEPSEEK_API_KEY"] == secret
    assert setup.env.read()["EMPTY"] == ""
    assert len(replacements) == 1 and not list(
        setup.env.path.parent.glob(".si-env-*.tmp")
    )
    # Missing non-editable profile defaults are filled, not overwritten.
    assert setup.env.read()["SI_RUNTIME_DB"] == str(setup.paths.database).replace(
        "\\", "/"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"UNKNOWN": "fake-secret"},
        {"DEEPSEEK_API_KEY": "fake-secret\nINJECT=1"},
        {"SI_RUNTIME_DB": "elsewhere"},
    ],
)
def test_invalid_input_safe_and_no_write(
    setup: SetupService, values: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(EnvEditError) as error:
        setup.env.update(values, expected="")
    assert "fake-secret" not in "".join(traceback.format_exception(error.value))
    assert "fake-secret" not in caplog.text and not setup.env.path.exists()


def test_concurrent_edit_and_atomic_failure_preserve_original(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup.env.path.write_text("UNKNOWN=existing\n", encoding="utf-8")
    with pytest.raises(EnvEditError):
        setup.env.update(KEYS, expected="")
    before = setup.env.path.read_bytes()

    def fail(*args):
        raise PermissionError(KEYS["DEEPSEEK_API_KEY"])

    monkeypatch.setattr(deployment_env.os, "replace", fail)
    with pytest.raises(EnvEditError) as error:
        setup.env.update(KEYS, expected=setup.env._text())
    assert KEYS["DEEPSEEK_API_KEY"] not in str(error.value)
    assert setup.env.path.read_bytes() == before
    assert len(list(setup.env.path.parent.glob("si.env.bak.*"))) == 1
    assert not list(setup.env.path.parent.glob(".si-env-*.tmp"))


def test_setup_preserves_identity_database_and_runtime_path(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = setup.paths.app / "data/characters/si_001.yaml"
    before_seed = seed.read_bytes()
    setup.paths.database.write_bytes(b"synthetic runtime marker; never initialize")
    before_db = setup.paths.database.read_bytes()
    calls = fake_check(monkeypatch, setup)
    result = setup.save(updates())
    assert result.saved and result.ready and calls == ["deploy_check"]
    assert seed.read_bytes() == before_seed
    assert setup.paths.database.read_bytes() == before_db


def test_none_transport_check_failure_and_optional_start(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_check(monkeypatch, setup, ok=False)
    result = setup.save(KEYS | {"SI_CHAT_TRANSPORT": "none"})
    assert result.saved and not result.ready and "cannot start" in result.message
    assert "Transport ERROR" in result.message
    assert not setup.start(confirmed=True).ok


def test_optional_start_permission_failure_is_safe(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_check(monkeypatch, setup)
    assert setup.save(updates()).ready
    assert not setup.start().ok
    result = setup.start(confirmed=True)
    assert not result.ok and "Configuration saved successfully" in result.message
    assert "Service start failed" in result.message and "sudo" in result.message
    assert len(calls) == 2


def test_check_command_isolated_env_and_redacted_output(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return CompletedProcess(
            arguments, 1, stdout="LLM MISSING", stderr=KEYS["DEEPSEEK_API_KEY"]
        )

    monkeypatch.setattr(setup_services.subprocess, "run", run)
    assert not setup.run_checks().ok
    args, options = calls[0]
    assert args == [
        sys.executable,
        "-m",
        "evolving_companion.deploy_check",
        "--env-file",
        str(setup.env.path),
        "--offline",
    ]
    assert options["env"] == setup.environment and "shell" not in options
    assert all(value not in repr(args) for value in KEYS.values())


def test_none_can_save_without_keys_but_cannot_start(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_check(monkeypatch, setup)
    result = setup.save({"SI_CHAT_TRANSPORT": "none"})
    assert result.saved and not result.ready
    assert setup.env.read()["DEEPSEEK_API_KEY"] == ""
    assert setup.env.read()["SILICONFLOW_API_KEY"] == ""
    assert not setup.start(confirmed=True).ok
    with pytest.raises(EnvEditError, match="keys are required"):
        setup.validate({"SI_CHAT_TRANSPORT": "qq"})


def test_cli_setup_route_and_help(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evolving_companion import setup_app

    monkeypatch.setattr(manager.sys, "platform", "linux")
    monkeypatch.setattr(
        manager.sys, "argv", ["si", "setup", "--env-file", str(setup.env.path)]
    )
    monkeypatch.setattr(setup_services, "SetupService", lambda *args: setup)
    called = []
    monkeypatch.setattr(setup_app.SetupApp, "run", lambda self: called.append("setup"))
    monkeypatch.setattr(
        manager,
        "load_server_env",
        lambda path: pytest.fail("Setup must not load stale environment"),
    )
    assert manager.main() == 0 and called == ["setup"]
    monkeypatch.setattr(manager.sys, "argv", ["si", "setup", "--help"])
    with pytest.raises(SystemExit) as error:
        manager.main()
    assert error.value.code == 0


def fill_form(screen: SetupScreen) -> None:
    for name, value in KEYS.items():
        assert screen.query_one(f"#secret-{name}", Input).password
        screen.query_one(f"#secret-{name}", Input).value = value
    screen.query_one("#transport", Select).value = "qq"
    screen.query_one("#value-SI_QQ_BOT_USER_ID", Input).value = "202"
    screen.query_one("#value-SI_QQ_ALLOWED_USER_IDS", Input).value = "101"


def test_setup_headless_save_review_skip_start_and_exit(
    setup: SetupService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_check(monkeypatch, setup)
    app = SetupApp(setup)

    async def smoke():
        async with app.run_test(size=(110, 50)) as pilot:
            await app.workers.wait_for_complete()
            screen = app.screen
            assert isinstance(screen, SetupScreen)
            assert "Missing" in str(screen.query_one("#welcome", Static).content)
            screen.query_one("#configure", Button).press()
            await pilot.pause()
            fill_form(screen)
            screen.query_one("#review-form", Button).press()
            await pilot.pause()
            assert all(
                value not in str(screen.query_one("#review-text", Static).content)
                for value in KEYS.values()
            )
            assert not setup.env.path.exists()
            screen.query_one("#save", Button).press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert setup.env.path.exists()
            assert all(
                value not in str(screen.query_one("#summary-text", Static).content)
                for value in KEYS.values()
            )
            assert all(
                screen.query_one(f"#secret-{name}", Input).value == "" for name in KEYS
            )
            screen.query_one("#finish", Button).press()
            await pilot.pause()
            assert not app.is_running

    asyncio.run(smoke())
    assert all(value not in caplog.text for value in KEYS.values())
    assert setup.env.read()["DEEPSEEK_API_KEY"] == KEYS["DEEPSEEK_API_KEY"]


def test_cancel_and_manager_shared_flow(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    import evolving_companion.manager_services as manager_services

    monkeypatch.setattr(
        manager_services, "check_deployment", lambda *args, **kwargs: ()
    )
    facade = DeploymentFacade(setup.paths, setup.service, {})
    assert facade.overview().setup_status == "Setup Required"
    app = SIManagerApp(facade, setup_service=setup)

    async def smoke():
        async with app.run_test(size=(110, 50)) as pilot:
            await app.workers.wait_for_complete()
            app.query_one("#setup", Button).press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, SetupScreen)
            app.screen.query_one("#configure", Button).press()
            await pilot.pause()
            fill_form(app.screen)
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert not isinstance(app.screen, SetupScreen)
            assert not setup.env.path.exists()
            await pilot.press("q")

    asyncio.run(smoke())


def test_parser_bad_input_and_existing_secret_never_prefilled(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(EnvEditError) as error:
        parse_env('BAD="fake-secret\n')
    assert "fake-secret" not in str(error.value)
    assert isinstance(setup.env, DeploymentEnvService)
    fake_check(monkeypatch, setup)
    assert setup.save(updates()).saved
    app = SetupApp(setup)

    async def smoke() -> None:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            screen = app.screen
            assert isinstance(screen, SetupScreen)
            for name in KEYS:
                assert screen.query_one(f"#secret-{name}", Input).value == ""
                assert screen.query_one(f"#mode-{name}", Select).value == "keep"
                assert screen.query_one(f"#secret-{name}", Input).disabled
            await pilot.press("escape")
            assert not app.is_running

    asyncio.run(smoke())


def test_linux_permission_boundary_is_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    target = tmp_path / "env"
    calls = []
    # Restore the real helper, only its OS operations are mocked.
    # Windows never performs an actual chmod/chown or requires a si Linux account.
    with monkeypatch.context() as scope:
        scope.setitem(
            sys.modules,
            "pwd",
            SimpleNamespace(
                getpwnam=lambda name: SimpleNamespace(pw_uid=123, pw_gid=456)
            ),
        )
        scope.setattr(deployment_env.os, "name", "posix")
        scope.setattr(
            deployment_env.os,
            "chown",
            lambda *args: calls.append(("owner", args)),
            raising=False,
        )
        scope.setattr(
            deployment_env.os, "chmod", lambda *args: calls.append(("mode", args))
        )
        REAL_PROTECT_FILE(target)
    assert calls == [("owner", (target, 123, 456)), ("mode", (target, 0o600))]


def test_explicit_environment_override_and_refresh(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup.environment["DEEPSEEK_API_KEY"] = "fake-explicit-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-explicit-key")
    # Register mutations for teardown before the manager refresh adds generic keys.
    for name in setup.env.defaults().keys() | {
        "SI_MEMORY_EMBEDDING_API_KEY",
        "SI_MEMORY_RERANKER_API_KEY",
    }:
        if name != "DEEPSEEK_API_KEY":
            monkeypatch.setenv(name, "")
    fake_check(monkeypatch, setup)
    assert setup.save(updates()).saved
    setup.refresh_process_environment()
    import os

    assert os.environ["DEEPSEEK_API_KEY"] == "fake-explicit-key"
    assert os.environ["SI_MEMORY_EMBEDDING_API_KEY"] == KEYS["SILICONFLOW_API_KEY"]
    assert "fake-explicit-key" not in setup.last_result.message
