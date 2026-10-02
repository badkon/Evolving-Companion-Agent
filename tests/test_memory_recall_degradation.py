"""Offline Conversation availability tests with real adapters and temporary SQLite."""

from collections.abc import Mapping, Sequence
from contextlib import closing, ExitStack
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import httpx
import pytest

from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation
from evolving_companion.memory_formation import MemoryFormationResult
from evolving_companion.memory_providers import (
    APIEmbeddingProvider,
    APIRerankerProvider,
)
from evolving_companion.memory_recall import MemoryRecallResult, MemoryRecallService
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval import (
    MemoryRetriever,
    MemoryRetrievalCandidate,
)
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore


class Client:
    def __init__(self) -> None:
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        return "正常回复"


def snapshot(store: SQLiteStore) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(store.path)) as connection:
        return {
            table: connection.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
            for table in (
                "memories",
                "memory_evidence",
                "memory_supersessions",
                "memory_embeddings",
            )
        }


@pytest.mark.parametrize("stage", ["index_build", "query_embedding", "reranker"])
@pytest.mark.parametrize("failure", ["timeout", "http", "malformed"])
def test_api_failure_degrades_and_next_turn_recovers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, stage: str, failure: str
) -> None:
    store = SQLiteStore(tmp_path / "recall.db")
    evidence = store.append_archive_message(str(uuid4()), "user", "合成 evidence")
    old = store.create_memory(
        "semantic", "旧计划", "explicit", "medium", evidence_refs=[evidence]
    )
    new = store.create_memory(
        "semantic", "用户不打算买 Mac。", "explicit", "medium", evidence_refs=[evidence]
    )
    store.supersede_memories([old.id], new.id)
    store.create_memory(
        "semantic",
        "用户生日是 10 月 7 日。",
        "explicit",
        "high",
        evidence_refs=[evidence],
    )
    calls: list[str] = []
    failing = False

    def respond(request: httpx.Request) -> httpx.Response:
        kind = "embedding" if request.url.path == "/embeddings" else "reranker"
        calls.append(kind)
        target = "reranker" if stage == "reranker" else "embedding"
        if failing and kind == target:
            if failure == "timeout":
                raise httpx.ReadTimeout("private-secret-detail", request=request)
            if failure == "http":
                return httpx.Response(503, text="private-secret-detail")
            return httpx.Response(200, json={"data": [], "results": []})
        payload = json.loads(request.content)
        if kind == "embedding":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": [1, 0]}
                        for index, _ in enumerate(payload["input"])
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": index, "relevance_score": 1.0}
                    for index, _ in enumerate(payload["documents"])
                ]
            },
        )

    with ExitStack() as resources:

        def embedding(model: str) -> APIEmbeddingProvider:
            provider = APIEmbeddingProvider(
                provider_id="fake-api",
                model_id=model,
                dimension=2,
                endpoint="https://offline.invalid/embeddings",
                api_key="fake-not-secret",
                transport=httpx.MockTransport(respond),
            )
            resources.callback(provider.close)
            return provider

        # Existing incompatible cache must survive a new-space build failure.
        MemoryRetriever(store, embedding("old-space")).rebuild_memory_embeddings()
        retriever = MemoryRetriever(store, embedding("current-space"))
        if stage != "index_build":
            retriever.rebuild_memory_embeddings()
        reranker = APIRerankerProvider(
            endpoint="https://offline.invalid/rerank",
            api_key="fake-not-secret",
            model_id="fake-rerank",
            transport=httpx.MockTransport(respond),
        )
        resources.callback(reranker.close)
        client = Client()
        conversation = Conversation(
            client,
            ProjectedCharacterContext("玲"),
            store,
            memory_recall_service=MemoryRecallService(
                retriever, MemoryReranker(provider=reranker)
            ),
        )
        before = snapshot(store)
        calls.clear()
        failing = True
        query = "我的生日是哪天？"
        assert conversation.send(query) == "正常回复"
        assert calls == (
            ["embedding", "reranker"] if stage == "reranker" else ["embedding"]
        )
        assert conversation.last_memory_recall_error == "MemoryProviderError"
        assert snapshot(store) == before
        with closing(sqlite3.connect(store.path)) as connection:
            assert connection.execute(
                "SELECT role, content FROM archive_messages WHERE conversation_id = ? ORDER BY rowid",
                (conversation.conversation_id,),
            ).fetchall() == [("user", query), ("assistant", "正常回复")]
        assert client.requests[0] == PromptBuilder(
            ProjectedCharacterContext("玲")
        ).build([], query)
        assert "memory_recall_failed: MemoryProviderError" in caplog.text
        assert "private-secret-detail" not in caplog.text
        assert "fake-not-secret" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)
        failing = False
        assert conversation.send(query) == "正常回复"
        assert conversation.last_memory_recall_error is None
        assert client.requests[1][1:3] == list(conversation.history[:2])
        assert "【可参考的长期记忆候选】" in client.requests[1][0]["content"]
        assert len(conversation.history) == 4


class FailingRecall:
    def recall(
        self, current_user_message: str, recent_messages: Sequence[Mapping[str, str]]
    ) -> MemoryRecallResult:
        raise sqlite3.OperationalError("private infrastructure detail")


def test_later_index_batch_failure_does_not_persist_partial_vectors(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "batch-failure.db")
    for content in ("用户计划购买电脑。", "用户想学习剪辑。"):
        store.create_memory("semantic", content, "explicit", "medium")
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    provider = APIEmbeddingProvider(
        provider_id="fake",
        model_id="v1",
        dimension=2,
        batch_size=1,
        endpoint="https://offline.invalid/embeddings",
        api_key="fake-not-secret",
        transport=httpx.MockTransport(respond),
    )
    with closing(provider):
        retriever = MemoryRetriever(store, provider)
        client = Client()
        conversation = Conversation(
            client,
            ProjectedCharacterContext("玲"),
            store,
            memory_recall_service=MemoryRecallService(retriever),
        )
        before = snapshot(store)
        assert conversation.send("我的计划是什么？") == "正常回复"
        assert calls == 2
        assert conversation.last_memory_recall_error == "MemoryProviderError"
        assert snapshot(store) == before
        assert "【可参考的长期记忆候选】" not in client.requests[0][0]["content"]


@pytest.mark.parametrize("formation_fails", [False, True])
def test_recall_infrastructure_failure_does_not_skip_formation(
    tmp_path: Path, formation_fails: bool
) -> None:
    store = SQLiteStore(tmp_path / "formation.db")

    class Formation:
        calls = 0

        def process_turn(
            self,
            user_archive_message: Mapping[str, str],
            assistant_archive_message: Mapping[str, str],
        ) -> MemoryFormationResult:
            self.calls += 1
            assert (
                assistant_archive_message["content"]
                == conversation.history[-1]["content"]
            )
            if formation_fails:
                raise RuntimeError("private formation detail")
            return MemoryFormationResult()

    formation = Formation()
    conversation = Conversation(
        Client(),
        ProjectedCharacterContext("玲"),
        store,
        memory_recall_service=FailingRecall(),
        memory_formation_service=formation,
    )
    assert conversation.send("我的生日是哪天？") == "正常回复"
    assert conversation.last_memory_recall_error == "OperationalError"
    assert formation.calls == 1
    result = conversation.last_memory_formation_result
    assert result is not None
    assert result.error == ("RuntimeError" if formation_fails else None)


@pytest.mark.parametrize("stage", ["user_archive", "llm", "assistant_archive"])
def test_main_completion_failures_still_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    store = SQLiteStore(tmp_path / "main-failure.db")
    original_append = store.append_archive_message

    def append(conversation_id: str, role: str, content: str) -> str:
        if stage == f"{role}_archive":
            raise OSError("main archive failure")
        return original_append(conversation_id, role, content)

    monkeypatch.setattr(store, "append_archive_message", append)
    client = Client()

    def fail_llm(messages: list[Message]) -> str:
        raise RuntimeError("main LLM failure")

    if stage == "llm":
        monkeypatch.setattr(client, "complete", fail_llm)
    conversation = Conversation(
        client,
        ProjectedCharacterContext("玲"),
        store,
        memory_recall_service=FailingRecall(),
    )
    with pytest.raises((OSError, RuntimeError)):
        conversation.send("我的生日是哪天？")
    assert conversation.history == ()


def test_need_false_has_no_provider_calls_or_diagnostic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Retriever:
        def retrieve(
            self, query: str, top_n: int = 10
        ) -> list[MemoryRetrievalCandidate]:
            raise AssertionError("Must not retrieve")

    class Reranker:
        def score(self, query: str, texts: Sequence[str]) -> Sequence[float]:
            raise AssertionError("Must not rerank")

    client = Client()
    conversation = Conversation(
        client,
        ProjectedCharacterContext("玲"),
        SQLiteStore(tmp_path / "no-need.db"),
        memory_recall_service=MemoryRecallService(
            Retriever(), MemoryReranker(provider=Reranker())
        ),
    )
    assert conversation.send("你好") == "正常回复"
    assert conversation.last_memory_recall_error is None
    assert "memory_recall_failed" not in caplog.text
    assert "【可参考的长期记忆候选】" not in client.requests[0][0]["content"]
