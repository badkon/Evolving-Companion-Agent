"""Lazy CPU CrossEncoder reranking for retrieved memory candidates."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evolving_companion.memory_retrieval import MemoryRetrievalCandidate

RERANKER_MODEL_NAME = "BAAI/bge-reranker-base"
RERANKER_DEVICE = "cpu"
RERANKER_BATCH_SIZE = 10
_CROSS_ENCODER: Any = None

ScoreFunction = Callable[[Sequence[tuple[str, str]]], Any]


@dataclass(frozen=True)
class RerankedMemoryCandidate:
    memory_id: str
    content: str
    memory_type: str
    source: str
    salience: str
    created_at: str
    semantic_similarity: float
    raw_score: float
    normalized_score: float


class MemoryReranker:
    """Rerank a semantic candidate set by raw CrossEncoder score."""

    def __init__(self, scorer: ScoreFunction | None = None) -> None:
        self._scorer = scorer

    def rerank(
        self,
        query: str,
        candidates: Sequence[MemoryRetrievalCandidate],
        *,
        top_n: int = 10,
    ) -> tuple[RerankedMemoryCandidate, ...]:
        if top_n <= 0:
            raise ValueError("top_n must be greater than zero")
        candidate_set = list(candidates[:top_n])
        if not candidate_set:
            return ()
        pairs = [(query, candidate.content) for candidate in candidate_set]
        raw_scores = self._score(pairs)
        if len(raw_scores) != len(candidate_set):
            raise ValueError("reranker must return one score per memory candidate")
        ranked = [
            RerankedMemoryCandidate(
                memory_id=candidate.memory_id,
                content=candidate.content,
                memory_type=candidate.memory_type,
                source=candidate.source,
                salience=candidate.salience,
                created_at=candidate.created_at,
                semantic_similarity=candidate.similarity,
                raw_score=raw_score,
                normalized_score=self._sigmoid(raw_score),
            )
            for candidate, raw_score in zip(candidate_set, raw_scores, strict=True)
        ]
        return tuple(
            sorted(ranked, key=lambda candidate: candidate.raw_score, reverse=True)
        )

    def _score(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._scorer is not None:
            values = self._scorer(pairs)
        else:
            values = self._score_with_model(pairs)
        import numpy as np

        scores = np.asarray(values, dtype=np.float64)
        if scores.ndim == 2 and scores.shape[1] == 1:
            scores = scores[:, 0]
        if scores.ndim != 1 or scores.size != len(pairs):
            raise ValueError("reranker must return one scalar score per candidate")
        if not np.isfinite(scores).all():
            raise ValueError("reranker returned a non-finite score")
        return [float(score) for score in scores]

    @staticmethod
    def _score_with_model(pairs: list[tuple[str, str]]) -> Any:
        model = MemoryReranker._load_model()
        from torch.nn import Identity

        return model.predict(
            pairs,
            activation_fn=Identity(),
            batch_size=RERANKER_BATCH_SIZE,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

    @staticmethod
    def _load_model() -> Any:
        global _CROSS_ENCODER
        if _CROSS_ENCODER is None:
            from sentence_transformers import CrossEncoder

            _CROSS_ENCODER = CrossEncoder(RERANKER_MODEL_NAME, device=RERANKER_DEVICE)
        return _CROSS_ENCODER

    @staticmethod
    def _sigmoid(raw_score: float) -> float:
        if not math.isfinite(raw_score):
            raise ValueError("reranker score must be finite")
        if raw_score >= 0:
            return 1.0 / (1.0 + math.exp(-raw_score))
        exponential = math.exp(raw_score)
        return exponential / (1.0 + exponential)
