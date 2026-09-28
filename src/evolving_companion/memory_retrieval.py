"""Local cosine-similarity retrieval over active SQLite memories."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import numpy as np

from evolving_companion.embeddings import (
    EMBEDDING_DIMENSIONS,
    MODEL_NAME,
    EmbeddingService,
)
from evolving_companion.storage import MemoryEmbedding, MemoryRecord, SQLiteStore


class EmbeddingEncoder(Protocol):
    def encode(self, texts: str | Sequence[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class MemoryRetrievalCandidate:
    memory_id: str
    content: str
    memory_type: str
    source: str
    salience: str
    created_at: str
    similarity: float


def _cached_vector(item: MemoryEmbedding) -> np.ndarray | None:
    if item.dimensions != EMBEDDING_DIMENSIONS:
        return None
    if len(item.embedding) != item.dimensions * np.dtype(np.float32).itemsize:
        return None
    vector = np.frombuffer(item.embedding, dtype=np.float32)
    if not np.isfinite(vector).all():
        return None
    if not np.isclose(np.linalg.norm(vector), 1.0, atol=1e-4):
        return None
    return vector


class MemoryRetriever:
    """Ensure active-memory vectors are cached, then return nearest candidates."""

    def __init__(
        self,
        store: SQLiteStore,
        embedding_service: EmbeddingEncoder | None = None,
    ) -> None:
        self._store = store
        self._embedding_service = embedding_service or EmbeddingService()

    def retrieve(self, query: str, top_n: int = 10) -> list[MemoryRetrievalCandidate]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_n <= 0:
            raise ValueError("top_n must be greater than zero")

        memories = self._store.list_active_memories()
        if not memories:
            return []

        vectors = self._ensure_active_embeddings(memories)
        query_vector = self._embedding_service.encode(query)
        if query_vector.shape != (1, EMBEDDING_DIMENSIONS):
            raise ValueError("query embedding has an unexpected shape")
        if not np.isfinite(query_vector).all() or not np.isclose(
            np.linalg.norm(query_vector[0]), 1.0, atol=1e-4
        ):
            raise ValueError("query embedding must be finite and normalized")

        # Both sides are normalized, so their dot product is cosine similarity.
        ranked = sorted(
            (
                (float(np.dot(query_vector[0], vectors[memory.id])), memory)
                for memory in memories
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [
            MemoryRetrievalCandidate(
                memory_id=memory.id,
                content=memory.content,
                memory_type=memory.memory_type,
                source=memory.source,
                salience=memory.salience,
                created_at=memory.created_at,
                similarity=similarity,
            )
            for similarity, memory in ranked[:top_n]
        ]

    def _ensure_active_embeddings(
        self, memories: Sequence[MemoryRecord]
    ) -> dict[str, np.ndarray]:
        model_name = MODEL_NAME
        memory_ids = [memory.id for memory in memories]
        cached = self._store.get_memory_embeddings(memory_ids, model_name)
        vectors: dict[str, np.ndarray] = {}
        missing: list[MemoryRecord] = []
        for memory in memories:
            item = cached.get(memory.id)
            vector = _cached_vector(item) if item is not None else None
            if vector is None:
                missing.append(memory)
            else:
                vectors[memory.id] = vector

        if missing:
            encoded = self._embedding_service.encode([item.content for item in missing])
            if encoded.shape != (len(missing), EMBEDDING_DIMENSIONS):
                raise ValueError("memory embeddings have an unexpected shape")
            if not np.isfinite(encoded).all():
                raise ValueError("memory embeddings contain non-finite values")
            norms = np.linalg.norm(encoded, axis=1)
            if not np.allclose(norms, 1.0, atol=1e-4):
                raise ValueError("memory embeddings must be normalized")

            created_at = datetime.now(timezone.utc).isoformat()
            cache_rows: list[MemoryEmbedding] = []
            for memory, vector in zip(missing, encoded, strict=True):
                normalized = np.asarray(vector, dtype=np.float32)
                vectors[memory.id] = normalized
                cache_rows.append(
                    MemoryEmbedding(
                        memory_id=memory.id,
                        model_name=model_name,
                        dimensions=EMBEDDING_DIMENSIONS,
                        embedding=normalized.tobytes(),
                        created_at=created_at,
                    )
                )
            self._store.upsert_memory_embeddings(cache_rows)
        return vectors
