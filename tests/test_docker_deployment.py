"""Docker deployment contracts; no daemon, remote API or model downloads."""

from pathlib import Path
import os
import shutil
import socket
import sys

import httpx
import pytest
import yaml

from evolving_companion import health
from evolving_companion.storage import SQLiteStore

REPO = Path(__file__).resolve().parents[1]


def service() -> dict:
    document = yaml.safe_load((REPO / "compose.yaml").read_text(encoding="utf-8"))
    assert set(document["services"]) == {"si-core"}
    return document["services"]["si-core"]


def test_image_uses_existing_api_only_entry_and_dependencies() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in dockerfile
    assert "WORKDIR /app" in dockerfile
    assert "pip install --no-cache-dir -e ." in dockerfile
    assert (
        'CMD ["python", "-m", "evolving_companion.server", "--env-file", "config/si.env"]'
        in dockerfile
    )
    assert "COPY src/ ./src/" in dockerfile
    assert "COPY config/si.env.example" in dockerfile
    for forbidden in (
        "local-memory]",
        "dev]",
        "apt-get",
        "git clone",
        ".env.local",
        "COPY . .",
    ):
        assert forbidden not in dockerfile


def test_compose_persists_existing_paths_and_external_transport() -> None:
    core = service()
    assert core["restart"] == "unless-stopped"
    assert core["init"] is True
    assert core["network_mode"] == "host"
    assert "ports" not in core and "privileged" not in core
    assert core["build"] == {"context": ".", "dockerfile": "Dockerfile"}
    mounts = {item["source"]: item for item in core["volumes"]}
    assert set(mounts) == {"./config", "./data", "./runtime", "./backups"}
    for source, item in mounts.items():
        assert item["type"] == "bind"
        assert item["target"] == "/app/" + source.removeprefix("./")
        assert item["bind"]["create_host_path"] is False
        assert item.get("read_only", False) == (source in {"./config", "./data"})
    assert core["read_only"] is True
    assert core["cap_drop"] == ["ALL"]


def test_compose_environment_is_non_secret_api_only() -> None:
    environment = service()["environment"]
    assert environment == {
        "SI_RUNTIME_DB": "runtime/si_001.db",
        "SI_BACKUP_DIR": "backups",
        "SI_MEMORY_EMBEDDING_PROVIDER": "api",
        "SI_MEMORY_RERANKER_PROVIDER": "api",
    }
    assert not any("KEY" in name or "TOKEN" in name for name in environment)


def test_healthcheck_reuses_offline_application_health() -> None:
    check = service()["healthcheck"]
    assert check["test"] == [
        "CMD",
        "python",
        "-m",
        "evolving_companion.health",
        "--env-file",
        "config/si.env",
        "--offline",
    ]
    assert check["timeout"] == "15s" and check["retries"] == 3


def test_build_context_allowlist_contains_only_required_resources() -> None:
    rules = [
        line
        for line in (REPO / ".dockerignore").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert rules[0] == "**"
    allowed = {line[1:] for line in rules[1:]}
    assert all(line.startswith("!") for line in rules[1:])
    assert allowed == {
        "Dockerfile",
        "pyproject.toml",
        "README.md",
        "src/",
        "src/evolving_companion/",
        "src/evolving_companion/*.py",
        "data/",
        "data/characters/",
        "data/characters/si_001.yaml",
        "data/worlds/",
        "data/worlds/si_world.yaml",
        "config/",
        "config/si.env.example",
    }
    for path in (
        "pyproject.toml",
        "README.md",
        "data/characters/si_001.yaml",
        "data/worlds/si_world.yaml",
        "config/si.env.example",
    ):
        assert (REPO / path).is_file()


@pytest.mark.parametrize("existing_db", [False, True])
def test_container_layout_offline_health_has_no_network_or_db_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    existing_db: bool,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("SI_") or name in {
            "DEEPSEEK_API_KEY",
            "SILICONFLOW_API_KEY",
        }:
            monkeypatch.delenv(name)
    for name in ("data/characters/si_001.yaml", "data/worlds/si_world.yaml"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, target)
    (tmp_path / "config").mkdir()
    shutil.copyfile(REPO / "config/si.env.example", tmp_path / "config/si.env")
    for folder in ("runtime", "backups"):
        (tmp_path / folder).mkdir()
    monkeypatch.chdir(tmp_path)
    for name, value in service()["environment"].items():
        monkeypatch.setenv(name, value)

    def forbidden(*args, **kwargs):
        raise AssertionError("No network in container health")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    database = tmp_path / "runtime/si_001.db"
    if existing_db:
        SQLiteStore(database)
    before = database.read_bytes() if existing_db else None
    seed_before = (tmp_path / "data/characters/si_001.yaml").read_bytes()
    monkeypatch.setattr(
        sys, "argv", ["health", "--offline", "--project-root", str(tmp_path)]
    )
    assert health.main() == 0
    output = capsys.readouterr().out
    assert "FAILED" not in output
    if not existing_db:
        assert "NOT INITIALIZED" in output
    assert (database.read_bytes() if existing_db else None) == before
    assert database.exists() == existing_db
    assert (tmp_path / "data/characters/si_001.yaml").read_bytes() == seed_before
