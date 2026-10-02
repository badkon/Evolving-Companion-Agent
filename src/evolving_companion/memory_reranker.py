"""Provider-independent score ordering for retrieved memory candidates."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evolving_companion.memory_retrieval import MemoryRetrievalCandidate
from evolving_companion.memory_providers import (
    LocalBGERerankerProvider,
    RerankerProvider,
)

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
    """Rerank a semantic candidate set by raw provider score (not probability)."""

    def __init__(
        self,
        scorer: ScoreFunction | None = None,
        *,
        provider: RerankerProvider | None = None,
    ) -> None:
        if scorer is not None and provider is not None:
            raise ValueError("provide a scorer or a provider, not both")
        self._scorer = scorer
        self._provider = provider or LocalBGERerankerProvider()

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
            values = self._provider.score(pairs[0][0], [text for _, text in pairs])
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
    def _sigmoid(raw_score: float) -> float:
        if not math.isfinite(raw_score):
            raise ValueError("reranker score must be finite")
        if raw_score >= 0:
            return 1.0 / (1.0 + math.exp(-raw_score))
        exponential = math.exp(raw_score)
        return exponential / (1.0 + exponential)
