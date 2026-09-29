import os
from pathlib import Path
from runpy import run_path

import pytest

from evolving_companion.local_env import load_local_env


def test_existing_process_environment_is_not_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("DEEPSEEK_API_KEY=file-value\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-value")

    assert load_local_env(env_file)
    assert os.environ["DEEPSEEK_API_KEY"] == "process-value"


def test_missing_process_environment_loads_explicit_local_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("DEEPSEEK_API_KEY=file-value\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert load_local_env(env_file)
    assert os.environ["DEEPSEEK_API_KEY"] == "file-value"


def test_missing_local_file_is_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert not load_local_env(tmp_path / "missing.env")


def test_fake_smoke_clients_do_not_require_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    repository_root = Path(__file__).parents[1]
    for script_name in (
        "run_memory_formation_smoke.py",
        "run_memory_consolidation_smoke.py",
        "run_memory_injection_smoke.py",
    ):
        namespace = run_path(str(repository_root / "scripts" / script_name))
        fake_client = namespace["FakeLLMClient"]()
        assert fake_client.complete([])
