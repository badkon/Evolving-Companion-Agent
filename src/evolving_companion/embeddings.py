"""Lazy, CPU-first embeddings for production memory retrieval."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

MODEL_NAME = "BAAI/bge-base-zh-v1.5"
EMBEDDING_DIMENSIONS = 768
_MODEL_INSTANCE = None


class EmbeddingService:
    """Encode text with one cached BGE model instance per service instance."""

    def _load_model(self):
        global _MODEL_INSTANCE
        if _MODEL_INSTANCE is None:
            from sentence_transformers import SentenceTransformer

            _MODEL_INSTANCE = SentenceTransformer(MODEL_NAME, device="cpu")
        return _MODEL_INSTANCE

    def encode(self, texts: str | Sequence[str]) -> np.ndarray:
        """Return normalized float32 vectors as a two-dimensional array."""
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
        if any(not item.strip() for item in items):
            raise ValueError("embedding text must not be empty")

        vectors = np.asarray(
            self._load_model().encode(
                items,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )
        if vectors.shape != (len(items), EMBEDDING_DIMENSIONS):
            raise ValueError(
                "embedding model returned unexpected shape "
                f"{vectors.shape}; expected ({len(items)}, {EMBEDDING_DIMENSIONS})"
            )
        if not np.isfinite(vectors).all():
            raise ValueError("embedding model returned non-finite values")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms == 0):
            raise ValueError("embedding model returned a zero vector")
        return np.asarray(vectors / norms, dtype=np.float32)
