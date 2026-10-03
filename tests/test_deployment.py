"""Offline, cross-platform deployment foundation tests; no Linux admin operations."""

from contextlib import closing
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tomllib
from uuid import uuid4

import httpx
import pytest

from evolving_companion import deploy_check, deployment, health, restore, server
from evolving_companion.backup import backup_database
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.deployment import (
    DeploymentPaths,
    check_deployment,
    check_sqlite,
)
from evolving_companion.storage import SQLiteStore

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DeploymentPaths:
    # Do not read or inherit the developer's real environment/secrets.
    for name in tuple(os.environ):
        if name.startswith("SI_") or name in {
            "DEEPSEEK_API_KEY",
            "SILICONFLOW_API_KEY",
        }:
            monkeypatch.delenv(name)
    root = tmp_path / "si"
    for directory in (
        "app/data/characters",
        "app/data/worlds",
        "app/deploy/systemd",
        "config",
        "runtime",
        "backups",
    ):
        (root / directory).mkdir(parents=True)
    for name in (
        "pyproject.toml",
        "uv.lock",
        "data/characters/si_001.yaml",
        "data/worlds/si_world.yaml",
        "deploy/systemd/si.service",
    ):
        shutil.copyfile(REPO / name, root / "app" / name)
    env_file = root / "config/si.env"
    template = (REPO / "deploy/si.env.example").read_text(encoding="utf-8")
    env_file.write_text(template.replace("/opt/si", root.as_posix()), encoding="utf-8")
    env_file.chmod(0o600)
    for line in template.splitlines():
        if line and not line.startswith("#"):
            name = line.split("=", 1)[0]
            monkeypatch.setenv(name, "")
            monkeypatch.delenv(name)
    monkeypatch.setenv("SI_DEPLOY_ROOT", str(root))
    monkeypatch.setenv("SI_RUNTIME_DB", str(root / "runtime/si_001.db"))
    for name, value in {
        "DEEPSEEK_API_KEY": "fake-test-key",
        "SILICONFLOW_API_KEY": "fake-silicon-key",
        "SI_QQ_BOT_USER_ID": "202",
        "SI_QQ_ALLOWED_USER_IDS": "101",
        "SI_CHAT_TRANSPORT": "qq",
    }.items():
        monkeypatch.setenv(name, value)
    # The loader will add these; register mutations for fixture teardown.
    for name in ("SI_MEMORY_EMBEDDING_API_KEY", "SI_MEMORY_RERANKER_API_KEY"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    deployment.load_server_env(env_file)
    # CI must not need a Linux si user. Real permission policy has separate tests.
    monkeypatch.setattr(deployment, "check_env_permissions", lambda path: None)
    return DeploymentPaths.from_environment(env_file)


def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Network must not be used")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)


@pytest.mark.parametrize("command", [deploy_check.main, health.main])
def test_checks_offline_and_no_runtime_or_seed_changes(
    configured: DeploymentPaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command,
) -> None:
    store = SQLiteStore(configured.database)
    store.create_memory("semantic", "合成持久记忆", "explicit", "medium")
    before_db = configured.database.read_bytes()
    seed_path = configured.app / "data/characters/si_001.yaml"
    before_seed = seed_path.read_bytes()
    internal_id = load_character_seed_data(seed_path).identity.internal_id
    deny_network(monkeypatch)
    original_import = __import__
    import builtins

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("torch", "transformers", "sentence_transformers"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(sys, "argv", ["check", "--env-file", str(configured.env_file)])
    assert command() == 0
    output = capsys.readouterr().out
    assert "CONFIGURED" in output and "FAILED" not in output
    assert "fake-test-key" not in output and "fake-silicon-key" not in output
    assert configured.database.read_bytes() == before_db
    assert seed_path.read_bytes() == before_seed
    assert load_character_seed_data(seed_path).identity.internal_id == internal_id


def test_fresh_deploy_probe_does_not_create_character_db(
    configured: DeploymentPaths,
) -> None:
    assert all(item.ok for item in check_deployment(configured))
    assert not configured.database.exists()
    result = check_deployment(configured, health=True)
    assert any(not item.ok and item.name == "Runtime DB (read-only)" for item in result)
    assert not configured.database.exists()
    assert not list(configured.database.parent.glob(".si-check-*"))


def test_offline_foundation_defers_keys_without_allowing_production_start(
    configured: DeploymentPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    deny_network(monkeypatch)
    for name in (
        "DEEPSEEK_API_KEY",
        "SILICONFLOW_API_KEY",
        "SI_MEMORY_EMBEDDING_API_KEY",
        "SI_MEMORY_RERANKER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SI_CHAT_TRANSPORT", "none")
    results = check_deployment(configured, offline=True, health=True)
    assert all(item.ok for item in results)
    assert any(item.status == "DEFERRED (offline)" for item in results)
    assert any("NOT INITIALIZED" in item.status for item in results)
    assert not all(item.ok for item in check_deployment(configured))
    assert not configured.database.exists()
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_PROVIDER", "local")
    assert not all(item.ok for item in check_deployment(configured, offline=True))


def test_offline_checks_do_not_hide_corrupt_database_or_invalid_config(
    configured: DeploymentPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured.database.write_bytes(b"not sqlite")
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_DIMENSION", "bad")
    results = check_deployment(configured, offline=True)
    assert any(item.name == "SQLite" and not item.ok for item in results)
    assert any(
        item.name == "Embedding / Reranker Provider" and not item.ok for item in results
    )


def test_missing_secrets_and_unsupported_transport_are_safe(
    configured: DeploymentPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setenv("SI_CHAT_TRANSPORT", "not-implemented")
    result = check_deployment(configured)
    assert any(
        item.name == "LLM API Key" and item.status == "MISSING" for item in result
    )
    assert any(item.name == "Transport" and not item.ok for item in result)


def test_identity_mismatch_is_rejected_without_reset(
    configured: DeploymentPaths,
) -> None:
    SQLiteStore(configured.database)
    with closing(sqlite3.connect(configured.database)) as connection, connection:
        connection.execute(
            "INSERT INTO character_runtime VALUES (?, ?, ?)",
            (str(uuid4()), None, "2026-01-01T00:00:00+00:00"),
        )
    before = configured.database.read_bytes()
    assert any(
        item.name == "Character Seed / Identity" and not item.ok
        for item in check_deployment(configured)
    )
    assert configured.database.read_bytes() == before


def test_runtime_path_separation_and_explicit_store_path(
    configured: DeploymentPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not configured.database.is_relative_to(configured.app)
    assert SQLiteStore().path == configured.database
    explicit = configured.root / "explicit.db"
    assert SQLiteStore(explicit).path == explicit
    bad = configured.app / "runtime/db.sqlite"
    bad.parent.mkdir()
    monkeypatch.setenv("SI_RUNTIME_DB", str(bad))
    bad_paths = DeploymentPaths.from_environment(configured.env_file)
    assert any(
        item.name.startswith("Runtime Directory") and not item.ok
        for item in check_deployment(bad_paths)
    )


def test_env_priority_and_no_systemd_interpolation(
    configured: DeploymentPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_API_KEY", "fake-explicit-override")
    deployment.load_server_env(configured.env_file)
    assert os.environ["DEEPSEEK_API_KEY"] == "fake-test-key"
    assert os.environ["SI_MEMORY_EMBEDDING_API_KEY"] == "fake-explicit-override"
    assert os.environ["SI_MEMORY_RERANKER_API_KEY"] == "fake-silicon-key"
    assert "${" not in (REPO / "deploy/si.env.example").read_text()


def test_systemd_assets_profile_and_installer_contract() -> None:
    unit = (REPO / "deploy/systemd/si.service").read_text()
    for line in (
        "User=si",
        "Group=si",
        "EnvironmentFile=/opt/si/config/si.env",
        "Restart=on-failure",
        "RestartPreventExitStatus=78",
        "ExecStart=/opt/si/app/.venv/bin/python -m evolving_companion.server",
    ):
        assert line in unit
    assert "Restart=always" not in unit
    env = (REPO / "deploy/si.env.example").read_text()
    assert "DEEPSEEK_API_KEY=\n" in env and "SILICONFLOW_API_KEY=\n" in env
    assert (
        "SI_MEMORY_EMBEDDING_PROVIDER=api" in env
        and "SI_MEMORY_RERANKER_PROVIDER=api" in env
    )
    install = (REPO / "deploy/install.sh").read_text()
    assert "if [[ ! -e /opt/si/config/si.env ]]; then" in install
    assert "--locked --no-dev --no-extra local-memory" in install
    assert (
        "chmod 777" not in install
        and "| bash\n" not in install
        and "| sh\n" not in install
    )
    assert "git pull" not in install and "uuid" not in install.lower()
    for script in (
        "install.sh",
        "backup.sh",
        "restore.sh",
        "si-service.sh",
        "si.sh",
        "validate_linux.sh",
    ):
        assert b"\r\n" not in (REPO / "deploy" / script).read_bytes()
    project = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert not any(
        "sentence-transformers" in dep for dep in project["project"]["dependencies"]
    )


def test_sqlite_online_backup_preserves_committed_wal(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE payload(value TEXT)")
        writer.execute("INSERT INTO payload VALUES ('committed')")
        writer.commit()
        backup = backup_database(source, tmp_path / "backups")
        assert backup.name.startswith("si_001_")
        with closing(sqlite3.connect(backup)) as copy:
            assert copy.execute("SELECT value FROM payload").fetchall() == [
                ("committed",)
            ]
        with pytest.raises(FileExistsError):
            # Reserve the same timestamp via a deterministic patched clock instead below.
            import evolving_companion.backup as module
            from unittest.mock import patch
            from datetime import datetime

            instant = datetime.strptime(
                backup.stem.removeprefix("si_001_"), "%Y%m%d_%H%M%S"
            )
            with patch.object(module, "datetime") as clock:
                clock.now.return_value = instant
                backup_database(source, tmp_path / "backups")


def test_restore_requires_stop_and_saves_pre_restore_backup(tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    with closing(sqlite3.connect(live)) as connection, connection:
        connection.execute("CREATE TABLE payload(value TEXT)")
        connection.execute("INSERT INTO payload VALUES ('old')")
    saved = backup_database(live, tmp_path / "backups")
    with closing(sqlite3.connect(live)) as connection, connection:
        connection.execute("UPDATE payload SET value='new'")
    with pytest.raises(ValueError, match="Stop"):
        restore.restore_database(
            saved, live, tmp_path / "backups", service_stopped=False
        )
    pre_restore = restore.restore_database(
        saved, live, tmp_path / "backups", service_stopped=True
    )
    assert pre_restore.name.startswith("pre_restore_")
    for path, expected in ((live, "old"), (pre_restore, "new")):
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT value FROM payload").fetchone() == (
                expected,
            )


@pytest.mark.parametrize("contents", [b"", b"not sqlite"])
def test_restore_invalid_backup_never_overwrites(
    tmp_path: Path, contents: bytes
) -> None:
    live = tmp_path / "live.db"
    SQLiteStore(live)
    before = live.read_bytes()
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(contents)
    with pytest.raises(ValueError):
        restore.restore_database(
            invalid, live, tmp_path / "backups", service_stopped=True
        )
    assert live.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        check_sqlite(tmp_path / "missing.db")


def test_restore_command_requires_explicit_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["restore"])
    with pytest.raises(SystemExit) as error:
        restore.main()
    assert error.value.code == 2


def test_restore_service_state_check_is_mockable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args=[], returncode=0, stdout="inactive\n"
        ),
    )
    assert restore.service_is_stopped()
    monkeypatch.setattr(
        restore.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args=[], returncode=0, stdout="active\n"
        ),
    )
    assert not restore.service_is_stopped()


def test_server_rejects_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.os, "geteuid", lambda: 0, raising=False)
    assert server.main() == 78


def test_restore_rejects_other_character_before_backup(tmp_path: Path) -> None:
    live, other = tmp_path / "live.db", tmp_path / "other.db"
    for path in (live, other):
        SQLiteStore(path)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "INSERT INTO character_runtime VALUES (?, ?, ?)",
                (str(uuid4()), None, "2026-01-01T00:00:00+00:00"),
            )
    before = live.read_bytes()
    with pytest.raises(ValueError, match="identity"):
        restore.restore_database(
            other, live, tmp_path / "backups", service_stopped=True
        )
    assert live.read_bytes() == before
    assert not (tmp_path / "backups").exists()


def test_server_selects_existing_qq_without_local_env(
    configured: DeploymentPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evolving_companion import qq_cli

    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(server, "load_server_env", lambda path: None)
    monkeypatch.setattr(server, "check_deployment", lambda paths: ())
    calls: list[bool] = []

    def run_qq(*, load_environment: bool = True) -> int:
        calls.append(load_environment)
        return 1

    monkeypatch.setattr(qq_cli, "main", run_qq)
    assert server.main() == 1
    assert calls == [False]
