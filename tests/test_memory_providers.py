from collections.abc import Sequence
from contextlib import closing
import builtins
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from types import SimpleNamespace
import sys

import httpx
import numpy as np
import pytest

from evolving_companion.embeddings import LocalBGEEmbeddingProvider
from evolving_companion.memory_provider_config import create_memory_providers
from evolving_companion.memory_providers import (
    APIEmbeddingProvider,
    APIRerankerProvider,
    EmbeddingProvider,
    LocalBGERerankerProvider,
    MemoryProviderError,
    RerankerProvider,
)
from evolving_companion.memory_recall import MemoryRecallService
from evolving_companion.memory_reranker import MemoryReranker
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.storage import MemoryEmbedding, SQLiteStore


class FakeEmbeddingProvider:
    def __init__(
        self, provider_id: str = "fake", model_id: str = "v1", dimension: int = 2
    ):
        self.provider_id = provider_id
        self.model_id = model_id
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        self.calls.append(list(texts))
        result = np.zeros((len(texts), self.dimension), dtype=np.float32)
        result[:, 0] = 1
        return result


class FakeRerankerProvider:
    def score(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        return [-100.0 + index for index in range(len(texts))]


def test_local_protocols_are_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("sentence_transformers", "torch", "transformers"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    embedding: EmbeddingProvider = LocalBGEEmbeddingProvider()
    reranker: RerankerProvider = LocalBGERerankerProvider()
    assert embedding.dimension == 768
    assert embedding.embed_texts([]).shape == (0, 768)
    assert reranker.score("query", []) == []


def test_api_embedding_batches_restores_order_normalizes() -> None:
    calls = []

    def respond(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers["Authorization"] == "Bearer fake-not-secret"
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [3, 4]}
                    for index in reversed(range(len(payload["input"])))
                ]
            },
        )

    provider = APIEmbeddingProvider(
        endpoint="https://offline.invalid/embed",
        api_key="fake-not-secret",
        provider_id="example",
        model_id="model",
        dimension=2,
        batch_size=2,
        timeout=7,
        transport=httpx.MockTransport(respond),
    )
    result = provider.embed_texts(["a", "b", "c"])
    assert result.dtype == np.float32
    assert np.allclose(result, [[0.6, 0.8]] * 3)
    assert [call["input"] for call in calls] == [["a", "b"], ["c"]]


def test_local_adapters_with_stub_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from evolving_companion import embeddings

    def encode(texts, **kwargs):
        assert kwargs["normalize_embeddings"] is True
        vectors = np.zeros((len(texts), 768))
        vectors[:, 0] = 2
        return vectors

    monkeypatch.setattr(embeddings, "_MODEL_INSTANCE", SimpleNamespace(encode=encode))
    vectors = LocalBGEEmbeddingProvider().embed_texts(["synthetic"])
    assert vectors.dtype == np.float32
    assert vectors[0, 0] == 1
    provider = LocalBGERerankerProvider()

    def predict(pairs, **kwargs):
        assert pairs == [("query", "a"), ("query", "b")]
        assert kwargs["batch_size"] == 10
        return np.array([-4, 2])

    provider._model = SimpleNamespace(predict=predict)
    monkeypatch.setitem(sys.modules, "torch.nn", SimpleNamespace(Identity=lambda: None))
    assert provider.score("query", ["a", "b"]) == [-4, 2]


def test_api_reranker_preserves_input_alignment() -> None:
    def respond(request):
        assert json.loads(request.content)["top_n"] == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": -8},
                    {"index": 0, "relevance_score": 12},
                ]
            },
        )

    provider = APIRerankerProvider(
        endpoint="https://offline.invalid/rerank",
        api_key="fake-not-secret",
        model_id="model",
        transport=httpx.MockTransport(respond),
    )
    assert provider.score("query", ["a", "b"]) == [12, -8]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 1, "relevance_score": 1}],
        [{"index": 0, "relevance_score": "NaN"}],
    ],
)
def test_reranker_rejects_invalid_response(rows) -> None:
    provider = APIRerankerProvider(
        endpoint="https://offline.invalid/rerank",
        api_key="fake-not-secret",
        model_id="model",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": rows})
        ),
    )
    with pytest.raises(MemoryProviderError):
        provider.score("query", ["text"])


@pytest.mark.parametrize(
    "failure", ["http", "timeout", "malformed", "dimension", "indices", "nan"]
)
def test_api_errors_do_not_leak_secrets(failure, caplog) -> None:
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("fake-secret-key", request=request)
        if failure == "http":
            return httpx.Response(401, text="fake-secret-key")
        data = {"data": [{"index": 0, "embedding": [1, 0]}]}
        if failure == "malformed":
            data = {}
        elif failure == "dimension":
            data["data"][0]["embedding"] = [1]
        elif failure == "indices":
            data["data"][0]["index"] = 1
        elif failure == "nan":
            data["data"][0]["embedding"] = ["NaN", 0]
        return httpx.Response(200, json=data)

    provider = APIEmbeddingProvider(
        endpoint="https://offline.invalid/embed",
        api_key="fake-secret-key",
        provider_id="example",
        model_id="model",
        dimension=2,
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(MemoryProviderError) as error:
        provider.embed_texts(["text"])
    assert "fake-secret-key" not in str(error.value) + caplog.text


@pytest.mark.parametrize("changed", ["provider", "model", "dimension"])
def test_cache_identity_switch_and_rebuild_preserve_authoritative_data(
    tmp_path: Path, changed: str
) -> None:
    path = tmp_path / "index.db"
    store = SQLiteStore(path)
    archive = store.append_archive_message(str(uuid4()), "user", "synthetic evidence")
    old = store.create_memory("semantic", "old", "explicit", "medium")
    active = store.create_memory("semantic", "active", "explicit", "medium")
    store.add_evidence(active.id, "archive_message", archive)
    store.supersede_memories([old.id], active.id)

    def authoritative():
        with closing(sqlite3.connect(path)) as connection:
            return [
                connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("memories", "memory_evidence", "memory_supersessions")
            ]

    before = authoritative()
    original = FakeEmbeddingProvider()
    retriever = MemoryRetriever(store, original)
    retriever.retrieve("query")
    retriever.retrieve("query")
    assert original.calls == [["active"], ["query"], ["query"]]
    switched = FakeEmbeddingProvider(
        provider_id="other" if changed == "provider" else "fake",
        model_id="v2" if changed == "model" else "v1",
        dimension=3 if changed == "dimension" else 2,
    )
    new_retriever = MemoryRetriever(store, switched)
    new_retriever.retrieve("query")
    assert switched.calls == [["active"], ["query"]]
    assert new_retriever.index_key != retriever.index_key
    assert new_retriever.rebuild_memory_embeddings() == 1
    assert switched.calls[-1] == ["active"]
    assert authoritative() == before


def test_corrupt_dimension_under_same_key_rebuilt(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "index.db")
    item = store.create_memory("semantic", "content", "explicit", "medium")
    provider = FakeEmbeddingProvider()
    retriever = MemoryRetriever(store, provider)
    store.upsert_memory_embeddings(
        [
            MemoryEmbedding(
                item.id,
                retriever.index_key,
                1,
                np.ones(1, dtype=np.float32).tobytes(),
                "now",
            )
        ]
    )
    retriever.retrieve("query")
    assert provider.calls[0] == ["content"]


def test_failed_rebuild_leaves_memories_and_existing_index_intact(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "index.db")
    record = store.create_memory("semantic", "content", "explicit", "medium")

    class FailingProvider(FakeEmbeddingProvider):
        fail = False

        def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
            if self.fail:
                raise MemoryProviderError("synthetic failure")
            return super().embed_texts(texts)

    provider = FailingProvider()
    retriever = MemoryRetriever(store, provider)
    retriever.retrieve("query")
    before = store.get_memory_embeddings([record.id], retriever.index_key)
    provider.fail = True
    with pytest.raises(MemoryProviderError):
        retriever.rebuild_memory_embeddings()
    assert store.get_memory_embeddings([record.id], retriever.index_key) == before
    assert store.list_active_memories() == (record,)


def test_top10_top3_and_no_score_threshold(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "index.db")
    for index in range(12):
        store.create_memory("semantic", f"synthetic {index}", "explicit", "medium")
    result = MemoryRecallService(
        MemoryRetriever(store, FakeEmbeddingProvider()),
        MemoryReranker(provider=FakeRerankerProvider()),
    ).recall("你还记得我的计划吗？", [])
    assert len(result.candidates) == 10
    assert len(result.recalled_memories) == 3
    assert [item.raw_score for item in result.recalled_memories] == [-91, -92, -93]


def test_api_only_configuration_never_initializes_local_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("sentence_transformers", "torch", "transformers"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    for name, value in {
        "SI_MEMORY_EMBEDDING_PROVIDER": "api",
        "SI_MEMORY_RERANKER_PROVIDER": "api",
        "SI_MEMORY_EMBEDDING_API_ID": "example",
        "SI_MEMORY_EMBEDDING_MODEL": "embed",
        "SI_MEMORY_EMBEDDING_DIMENSION": "2",
        "SI_MEMORY_EMBEDDING_API_URL": "https://offline.invalid/embed",
        "SI_MEMORY_EMBEDDING_API_KEY": "fake-not-secret",
        "SI_MEMORY_RERANKER_MODEL": "rerank",
        "SI_MEMORY_RERANKER_API_URL": "https://offline.invalid/rerank",
        "SI_MEMORY_RERANKER_API_KEY": "fake-not-secret",
    }.items():
        monkeypatch.setenv(name, value)
    embedding, reranker = create_memory_providers()
    assert isinstance(embedding, APIEmbeddingProvider)
    assert isinstance(reranker, APIRerankerProvider)
