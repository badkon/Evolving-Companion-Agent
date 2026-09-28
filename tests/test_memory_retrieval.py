import sqlite3
from pathlib import Path

import numpy as np
import pytest

from evolving_companion.embeddings import EMBEDDING_DIMENSIONS, MODEL_NAME
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.storage import MemoryEmbedding, SQLiteStore


class FakeEmbeddingService:
    def __init__(self, vectors: dict[str, np.ndarray]) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def encode(self, texts: str | list[str]) -> np.ndarray:
        items = [texts] if isinstance(texts, str) else list(texts)
        self.calls.append(items)
        return np.stack([self.vectors[item] for item in items]).astype(np.float32)


def basis(index: int) -> np.ndarray:
    vector = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
    vector[index] = 1.0
    return vector


def add_memory(store: SQLiteStore, content: str, status: str = "active") -> str:
    return store.create_memory(
        "semantic", content, "explicit", "medium", status=status
    ).id


def test_active_memories_are_batch_embedded_and_cached_for_reuse(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    first_id = add_memory(store, "第一条")
    second_id = add_memory(store, "第二条")
    archived_id = add_memory(store, "归档记忆", "archived")
    service = FakeEmbeddingService(
        {"第一条": basis(0), "第二条": basis(1), "问题": basis(0)}
    )
    retriever = MemoryRetriever(store, service)

    result = retriever.retrieve("问题")

    assert [candidate.memory_id for candidate in result] == [first_id, second_id]
    assert service.calls == [["第一条", "第二条"], ["问题"]]
    assert archived_id not in {candidate.memory_id for candidate in result}
    cached = store.get_memory_embeddings([first_id, second_id], MODEL_NAME)
    assert set(cached) == {first_id, second_id}
    assert all(item.dimensions == EMBEDDING_DIMENSIONS for item in cached.values())
    assert all(
        len(item.embedding) == EMBEDDING_DIMENSIONS * 4 for item in cached.values()
    )

    retriever.retrieve("问题")

    assert service.calls[-1] == ["问题"]
    assert len(service.calls) == 3


def test_retrieval_uses_cosine_order_and_top_n(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    lower_id = add_memory(store, "较不相关")
    higher_id = add_memory(store, "更相关")
    query = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
    query[:2] = [0.6, 0.8]
    service = FakeEmbeddingService(
        {"较不相关": basis(0), "更相关": basis(1), "query": query}
    )

    result = MemoryRetriever(store, service).retrieve("query", top_n=1)

    assert [candidate.memory_id for candidate in result] == [higher_id]
    assert result[0].similarity == pytest.approx(0.8)
    assert lower_id not in {candidate.memory_id for candidate in result}


def test_no_active_memories_returns_empty_without_loading_embeddings(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    add_memory(store, "已被归档", "archived")
    add_memory(store, "已被 supersede", "superseded")
    service = FakeEmbeddingService({})

    assert MemoryRetriever(store, service).retrieve("query") == []
    assert service.calls == []


def test_model_mismatch_and_corrupt_vectors_are_rebuilt(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    first_id = add_memory(store, "旧模型缓存")
    second_id = add_memory(store, "损坏缓存")
    store.upsert_memory_embeddings(
        [
            MemoryEmbedding(
                first_id, "old-model", EMBEDDING_DIMENSIONS, basis(0).tobytes(), "now"
            ),
            MemoryEmbedding(
                second_id,
                MODEL_NAME,
                2,
                np.array([1, 0], dtype=np.float32).tobytes(),
                "now",
            ),
        ]
    )
    service = FakeEmbeddingService(
        {
            "旧模型缓存": basis(0),
            "损坏缓存": basis(1),
            "query": basis(1),
        }
    )

    result = MemoryRetriever(store, service).retrieve("query")

    assert service.calls[0] == ["旧模型缓存", "损坏缓存"]
    assert result[0].memory_id == second_id
    repaired = store.get_memory_embeddings([first_id, second_id], MODEL_NAME)
    assert set(repaired) == {first_id, second_id}
    assert all(item.dimensions == EMBEDDING_DIMENSIONS for item in repaired.values())
    assert np.array_equal(
        np.frombuffer(repaired[first_id].embedding, dtype=np.float32), basis(0)
    )


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
def test_empty_query_is_rejected(tmp_path: Path, query: str) -> None:
    store = SQLiteStore(tmp_path / "memory.db")
    service = FakeEmbeddingService({})

    with pytest.raises(ValueError, match="query"):
        MemoryRetriever(store, service).retrieve(query)


def test_non_positive_top_n_is_rejected(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "memory.db")

    with pytest.raises(ValueError, match="top_n"):
        MemoryRetriever(store, FakeEmbeddingService({})).retrieve("query", top_n=0)


def test_memory_embedding_blob_round_trip_through_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = SQLiteStore(path)
    memory_id = add_memory(store, "向量往返")
    vector = basis(17)
    item = MemoryEmbedding(
        memory_id,
        MODEL_NAME,
        EMBEDDING_DIMENSIONS,
        vector.tobytes(),
        "2026-01-01T00:00:00+00:00",
    )

    store.upsert_memory_embeddings([item])

    loaded = store.get_memory_embeddings([memory_id], MODEL_NAME)[memory_id]
    assert isinstance(loaded.embedding, bytes)
    assert np.array_equal(np.frombuffer(loaded.embedding, dtype=np.float32), vector)
    with sqlite3.connect(path) as connection:
        count = connection.execute("SELECT count(*) FROM memories").fetchone()[0]
        assert count == 1
