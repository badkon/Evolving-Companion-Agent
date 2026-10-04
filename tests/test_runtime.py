"""Core-only lifetime: temporary SQLite, no transport, network or real secrets."""

import asyncio
from contextlib import ExitStack
import signal
from pathlib import Path
import sqlite3
from contextlib import closing

import httpx
import pytest

from evolving_companion import runtime, qq_cli
from evolving_companion.character_data import load_character_seed_data


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_idle_wait_blocks_once_until_signal_and_restores_handlers(
    monkeypatch: pytest.MonkeyPatch, signum: signal.Signals
) -> None:
    waits: list[bool] = []
    real_event = asyncio.Event

    class CountingEvent(real_event):
        async def wait(self) -> bool:
            waits.append(True)
            return await super().wait()

    monkeypatch.setattr(runtime.asyncio, "Event", CountingEvent)

    async def scenario() -> None:
        handlers = {
            item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)
        }
        task = asyncio.create_task(runtime.wait_for_shutdown())
        await asyncio.sleep(0.02)
        assert not task.done()  # No one-shot initialization or terminal input.
        assert waits == [True]  # Exactly one blocking wait; no polling/busy loop.
        signal.raise_signal(signum)
        await asyncio.wait_for(task, 1)
        for item, handler in handlers.items():
            assert signal.getsignal(item) == handler

    asyncio.run(scenario())


def test_cancelled_idle_restores_signal_handlers() -> None:
    async def scenario() -> None:
        handlers = {
            item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)
        }
        task = asyncio.create_task(runtime.wait_for_shutdown())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for item, handler in handlers.items():
            assert signal.getsignal(item) == handler

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [None, "startup", "idle"])
def test_core_only_owns_resources_and_never_initializes_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    closed: list[bool] = []
    core = object()

    def initialize(resources: ExitStack) -> object:
        resources.callback(closed.append, True)
        if failure == "startup":
            raise ValueError("fake error")
        return core

    async def idle() -> None:
        assert not closed
        if failure == "idle":
            raise RuntimeError("fake error")

    def forbidden(*args, **kwargs):
        raise AssertionError("No QQ, stdin or external network")

    monkeypatch.setattr(runtime, "create_conversation", initialize)
    monkeypatch.setattr(runtime, "wait_for_shutdown", idle)
    monkeypatch.setattr(qq_cli, "main", forbidden)
    monkeypatch.setattr(qq_cli, "OneBotWebSocketTransport", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    assert runtime.run_core_only() == (1 if failure else 0)
    assert closed == [True]


@pytest.mark.parametrize(
    "pipeline_mode", ["natural", "natural_full", "natural_simplified", "legacy"]
)
def test_shared_core_initializes_temporary_database_without_api_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pipeline_mode: str
) -> None:
    assert qq_cli.create_conversation is runtime.create_conversation
    database = tmp_path / "core.db"
    monkeypatch.setenv("SI_RUNTIME_DB", str(database))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-test-key")
    monkeypatch.setenv("SI_REPLY_PIPELINE", pipeline_mode)
    monkeypatch.setenv("SI_MEMORY_EMBEDDING_PROVIDER", "api")
    monkeypatch.setenv("SI_MEMORY_RERANKER_PROVIDER", "api")
    for name, value in {
        "SI_MEMORY_EMBEDDING_API_ID": "siliconflow-cn",
        "SI_MEMORY_EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-8B",
        "SI_MEMORY_EMBEDDING_DIMENSION": "4096",
        "SI_MEMORY_EMBEDDING_API_URL": "https://api.siliconflow.cn/v1/embeddings",
        "SI_MEMORY_RERANKER_MODEL": "Qwen/Qwen3-Reranker-8B",
        "SI_MEMORY_RERANKER_API_URL": "https://api.siliconflow.cn/v1/rerank",
        "SI_MEMORY_EMBEDDING_API_KEY": "fake-test-key",
        "SI_MEMORY_RERANKER_API_KEY": "fake-test-key",
    }.items():
        monkeypatch.setenv(name, value)

    def forbidden(*args, **kwargs):
        raise AssertionError("No API call or transport during initialization")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(qq_cli, "OneBotWebSocketTransport", forbidden)
    seed = load_character_seed_data(
        Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    )
    with ExitStack() as resources:
        core = runtime.create_conversation(resources)
        assert core.history == ()
        assert (core._reply_pipeline is not None) == (pipeline_mode != "legacy")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT character_id FROM character_life_context"
        ).fetchone() == (str(seed.identity.internal_id),)
        assert connection.execute(
            "SELECT COUNT(*) FROM archive_messages"
        ).fetchone() == (0,)
