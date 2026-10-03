"""Application-only offline checks and SQLite backup/restore tests."""

from contextlib import closing
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
from uuid import uuid4
import httpx
import pytest
from evolving_companion import runtime_check, runtime_config, health, restore, server
from evolving_companion.backup import backup_database
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.runtime_config import RuntimePaths, check_runtime, check_sqlite
from evolving_companion.storage import SQLiteStore

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimePaths:
    for name in tuple(os.environ):
        if name.startswith("SI_") or name in {
            "DEEPSEEK_API_KEY",
            "SILICONFLOW_API_KEY",
        }:
            monkeypatch.delenv(name)
    project = tmp_path / "project"
    for directory in ("data/characters", "data/worlds", "config", "runtime", "backups"):
        (project / directory).mkdir(parents=True)
    for name in ("data/characters/si_001.yaml", "data/worlds/si_world.yaml"):
        shutil.copyfile(REPO / name, project / name)
    template = (REPO / "config/si.env.example").read_text(encoding="utf-8")
    env_file = project / "config/si.env"
    env_file.write_text(template, encoding="utf-8")
    for name, value in {
        "SI_RUNTIME_DB": str(project / "runtime/si_001.db"),
        "SI_BACKUP_DIR": str(project / "backups"),
        "DEEPSEEK_API_KEY": "fake-test-key",
        "SILICONFLOW_API_KEY": "fake-silicon-key",
        "SI_QQ_BOT_USER_ID": "202",
        "SI_QQ_ALLOWED_USER_IDS": "101",
        "SI_CHAT_TRANSPORT": "qq",
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("SI_MEMORY_EMBEDDING_API_KEY", "SI_MEMORY_RERANKER_API_KEY"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    runtime_config.load_runtime_env(env_file)
    return RuntimePaths.from_environment(env_file, project)


def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Network must not be used")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)


@pytest.mark.parametrize("command", [runtime_check.main, health.main])
def test_checks_offline_and_no_runtime_or_seed_changes(
    configured: RuntimePaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command,
) -> None:
    store = SQLiteStore(configured.database)
    store.create_memory("semantic", "合成持久记忆", "explicit", "medium")
    before_db = configured.database.read_bytes()
    seed_path = configured.project / "data/characters/si_001.yaml"
    before_seed = seed_path.read_bytes()
    internal_id = load_character_seed_data(seed_path).identity.internal_id
    deny_network(monkeypatch)
    original_import = __import__
    import builtins

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("torch", "transformers", "sentence_transformers"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check",
            "--env-file",
            str(configured.env_file),
            "--project-root",
            str(configured.project),
        ],
    )
    assert command() == 0
    output = capsys.readouterr().out
    assert "CONFIGURED" in output and "FAILED" not in output
    assert "fake-test-key" not in output and "fake-silicon-key" not in output
    assert configured.database.read_bytes() == before_db
    assert seed_path.read_bytes() == before_seed
    assert load_character_seed_data(seed_path).identity.internal_id == internal_id


def test_fresh_runtime_probe_does_not_create_character_db(
    configured: RuntimePaths,
) -> None:
    assert all(item.ok for item in check_runtime(configured))
    assert not configured.database.exists()
    result = check_runtime(configured, health=True)
    assert any(not item.ok and item.name == "Runtime DB (read-only)" for item in result)
    assert not configured.database.exists()
    assert not list(configured.database.parent.glob(".si-check-*"))


def test_offline_check_defers_keys_without_allowing_production_start(
    configured: RuntimePaths, monkeypatch: pytest.MonkeyPatch
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
    results = check_runtime(configured, offline=True, health=True)
    assert all(item.ok for item in results)
    assert any(item.status == "DEFERRED (offline)" for item in results)
    assert any("NOT INITIALIZED" in item.status for item in results)
    assert not all(item.ok for item in check_runtime(configured))
    assert not configured.database.exists()
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_PROVIDER", "local")
    assert not all(item.ok for item in check_runtime(configured, offline=True))


def test_api_only_entry_dispatches_without_loading_development_env(
    configured: RuntimePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evolving_companion import qq_cli, runtime

    deny_network(monkeypatch)
    calls: list[bool] = []

    def fake_qq(*, load_environment: bool = True) -> int:
        calls.append(load_environment)
        return 7

    monkeypatch.setattr(qq_cli, "main", fake_qq)
    monkeypatch.setattr(sys, "argv", ["server", "--env-file", str(configured.env_file)])
    assert server.main() == 7
    assert calls == [False]
    monkeypatch.setenv("SI_CHAT_TRANSPORT", "none")
    core_calls: list[bool] = []

    def fake_core_only() -> int:
        core_calls.append(True)
        return 0

    monkeypatch.setattr(runtime, "run_core_only", fake_core_only)
    assert server.main() == 0
    assert calls == [False] and core_calls == [True]
    monkeypatch.setenv("SI_CHAT_TRANSPORT", "unsupported")
    assert server.main() == 78
    assert calls == [False]
    assert not configured.database.exists()


def test_none_runtime_requires_providers_but_not_qq_configuration(
    configured: RuntimePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    deny_network(monkeypatch)
    monkeypatch.setenv("SI_CHAT_TRANSPORT", "none")
    for name in (
        "SI_QQ_BOT_USER_ID",
        "SI_QQ_ALLOWED_USER_IDS",
        "SI_ONEBOT_WS_URL",
        "SI_ONEBOT_ACCESS_TOKEN",
    ):
        monkeypatch.setenv(name, "invalid-unused-value")
    assert all(item.ok for item in check_runtime(configured))
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert not all(item.ok for item in check_runtime(configured))


def test_runtime_paths_have_no_installation_root_assumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SI_RUNTIME_DB", raising=False)
    monkeypatch.delenv("SI_BACKUP_DIR", raising=False)
    paths = RuntimePaths.from_environment(project_root=tmp_path)
    assert paths.database == tmp_path / "runtime/si_001.db"
    assert paths.backups == tmp_path / "backups"
    assert paths.env_file == tmp_path / "config/si.env"


def test_offline_checks_do_not_hide_corrupt_database_or_invalid_config(
    configured: RuntimePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured.database.write_bytes(b"not sqlite")
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_DIMENSION", "bad")
    results = check_runtime(configured, offline=True)
    assert any(item.name == "SQLite" and not item.ok for item in results)
    assert any(
        item.name == "Embedding / Reranker Provider" and not item.ok for item in results
    )


def test_missing_secrets_and_unsupported_transport_are_safe(
    configured: RuntimePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setenv("SI_CHAT_TRANSPORT", "not-implemented")
    result = check_runtime(configured)
    assert any(
        item.name == "LLM API Key" and item.status == "MISSING" for item in result
    )
    assert any(item.name == "Transport" and not item.ok for item in result)


def test_identity_mismatch_is_rejected_without_reset(
    configured: RuntimePaths,
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
        for item in check_runtime(configured)
    )
    assert configured.database.read_bytes() == before


def test_env_priority_and_no_interpolation(
    configured: RuntimePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_API_KEY", "fake-explicit-override")
    runtime_config.load_runtime_env(configured.env_file)
    assert os.environ["DEEPSEEK_API_KEY"] == "fake-test-key"
    assert os.environ["SI_MEMORY_EMBEDDING_API_KEY"] == "fake-explicit-override"
    assert os.environ["SI_MEMORY_RERANKER_API_KEY"] == "fake-silicon-key"
    assert "${" not in (REPO / "config/si.env.example").read_text()


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
            saved, live, tmp_path / "backups", writers_stopped=False
        )
    pre_restore = restore.restore_database(
        saved, live, tmp_path / "backups", writers_stopped=True
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
            invalid, live, tmp_path / "backups", writers_stopped=True
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
            other, live, tmp_path / "backups", writers_stopped=True
        )
    assert live.read_bytes() == before
    assert not (tmp_path / "backups").exists()
