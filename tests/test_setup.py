"""First-run setup tests use fake secrets, temporary paths, and no network."""

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

from evolving_companion import config_env, setup as setup_entry, setup_services
from evolving_companion.config_env import (
    ApplicationEnvService,
    EnvEditError,
    parse_env,
)
from evolving_companion.config_env import OperationResult
from evolving_companion.setup_app import SetupApp, SetupScreen
from evolving_companion.setup_services import SetupService

KEYS = {
    "DEEPSEEK_API_KEY": "fake-deepseek-secret",
    "SILICONFLOW_API_KEY": "fake-silicon-secret",
    "SI_ONEBOT_ACCESS_TOKEN": "fake-onebot-token",
}


def test_setup_cli_is_cross_platform_and_never_starts_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from evolving_companion import setup_app

    calls = []
    monkeypatch.setattr(
        setup_entry,
        "SetupService",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(setup_app.SetupApp, "run", lambda self: None)
    monkeypatch.setattr(
        sys, "argv", ["si", "setup", "--env-file", str(tmp_path / "si.env")]
    )
    assert setup_entry.main() == 0
    assert calls[0][0] == (tmp_path / "si.env",)
    monkeypatch.setattr(sys, "argv", ["si", "--help"])
    with pytest.raises(SystemExit) as error:
        setup_entry.main()
    assert error.value.code == 0


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("No real network in setup tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(setup_services.subprocess, "run", forbidden)
    # Inspect file protection separately from configuration behavior.
    monkeypatch.setattr(config_env, "protect_file", lambda path: None)


@pytest.fixture
def setup(tmp_path: Path) -> SetupService:
    root = tmp_path / "si"
    for directory in ("config", "data/characters", "runtime"):
        (root / directory).mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    template = (repo / "config/si.env.example").read_text(encoding="utf-8")
    (root / "config/si.env.example").write_text(
        template.replace("runtime/si_001.db", (root / "runtime/si_001.db").as_posix()),
        encoding="utf-8",
    )
    shutil.copyfile(
        repo / "data/characters/si_001.yaml", root / "data/characters/si_001.yaml"
    )
    return SetupService(root / "config/si.env", environment={}, project_root=root)


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
        calls.append("runtime_check")
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
    setup = SetupService(
        setup.env.path, environment=setup.environment, project_root=setup.paths.project
    )
    fake_check(monkeypatch, setup)
    assert setup.save(updates()).saved
    detection = setup.detect()
    assert detection.status == "Configured"
    assert "Existing application configuration detected" in detection.message
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
    monkeypatch.setattr(config_env, "protect_file", protection.append)
    replacements = []
    replace = config_env.os.replace

    def atomic(source, target):
        replacements.append((source, target))
        assert Path(source).parent == Path(target).parent
        assert Path(source) in protection
        assert Path(target).read_text(encoding="utf-8") == original
        replace(source, target)

    monkeypatch.setattr(config_env.os, "replace", atomic)
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

    monkeypatch.setattr(config_env.os, "replace", fail)
    with pytest.raises(EnvEditError) as error:
        setup.env.update(KEYS, expected=setup.env._text())
    assert KEYS["DEEPSEEK_API_KEY"] not in str(error.value)
    assert setup.env.path.read_bytes() == before
    assert len(list(setup.env.path.parent.glob("si.env.bak.*"))) == 1
    assert not list(setup.env.path.parent.glob(".si-env-*.tmp"))


def test_setup_preserves_identity_database_and_runtime_path(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = setup.paths.project / "data/characters/si_001.yaml"
    before_seed = seed.read_bytes()
    setup.paths.database.write_bytes(b"synthetic runtime marker; never initialize")
    before_db = setup.paths.database.read_bytes()
    calls = fake_check(monkeypatch, setup)
    result = setup.save(updates())
    assert result.saved and result.ready and calls == ["runtime_check"]
    assert seed.read_bytes() == before_seed
    assert setup.paths.database.read_bytes() == before_db


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
        "evolving_companion.runtime_check",
        "--env-file",
        str(setup.env.path),
        "--project-root",
        str(setup.paths.project),
        "--offline",
    ]
    assert options["env"] == setup.environment and "shell" not in options
    assert all(value not in repr(args) for value in KEYS.values())


def test_none_can_save_without_keys(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_check(monkeypatch, setup)
    result = setup.save({"SI_CHAT_TRANSPORT": "none"})
    assert result.saved and not result.ready
    assert setup.env.read()["DEEPSEEK_API_KEY"] == ""
    assert setup.env.read()["SILICONFLOW_API_KEY"] == ""
    with pytest.raises(EnvEditError, match="keys are required"):
        setup.validate({"SI_CHAT_TRANSPORT": "qq"})


def test_setup_explicit_environment_wins_over_file(setup: SetupService) -> None:
    setup.env.path.write_text("DEEPSEEK_API_KEY=fake-file-key\n", encoding="utf-8")
    service = SetupService(
        setup.env.path,
        environment={"DEEPSEEK_API_KEY": "fake-process-key"},
        project_root=setup.paths.project,
    )
    assert service.effective()["DEEPSEEK_API_KEY"] == "fake-process-key"
    assert service.env.read()["DEEPSEEK_API_KEY"] == "fake-file-key"


def fill_form(screen: SetupScreen) -> None:
    for name, value in KEYS.items():
        assert screen.query_one(f"#secret-{name}", Input).password
        screen.query_one(f"#secret-{name}", Input).value = value
    screen.query_one("#transport", Select).value = "qq"
    screen.query_one("#value-SI_QQ_BOT_USER_ID", Input).value = "202"
    screen.query_one("#value-SI_QQ_ALLOWED_USER_IDS", Input).value = "101"


def test_setup_headless_save_review_and_exit(
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


def test_parser_bad_input_and_existing_secret_never_prefilled(
    setup: SetupService, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(EnvEditError) as error:
        parse_env('BAD="fake-secret\n')
    assert "fake-secret" not in str(error.value)
    assert isinstance(setup.env, ApplicationEnvService)
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
