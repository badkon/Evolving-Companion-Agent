"""Pure scoring and reporting helpers for the local reranker experiment."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

import numpy as np


def parse_raw_scores(scores: Any, expected_count: int) -> list[float]:
    """Parse CrossEncoder outputs as one finite raw logit per candidate."""
    values = np.asarray(scores, dtype=np.float64)
    if values.ndim == 2 and values.shape[1] == 1:
        values = values[:, 0]
    if values.ndim != 1 or values.size != expected_count:
        raise ValueError(
            f"expected {expected_count} scalar reranker scores, got shape {values.shape}"
        )
    if not np.isfinite(values).all():
        raise ValueError("reranker returned a non-finite score")
    return [float(value) for value in values]


def normalize_raw_score(raw_score: float) -> float:
    """Apply a numerically stable sigmoid to a raw cross-encoder logit."""
    if not math.isfinite(raw_score):
        raise ValueError("reranker score must be finite")
    if raw_score >= 0:
        return 1.0 / (1.0 + math.exp(-raw_score))
    exponential = math.exp(raw_score)
    return exponential / (1.0 + exponential)


def rerank_candidates(
    semantic_candidates: Sequence[Mapping[str, Any]],
    raw_scores: Any,
    *,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Attach cross-encoder scores and reorder only the semantic Top-N set."""
    if top_n <= 0:
        raise ValueError("top_n must be greater than zero")
    candidate_set = semantic_candidates[:top_n]
    parsed_scores = parse_raw_scores(raw_scores, len(candidate_set))
    scored = [
        {
            **candidate,
            "semantic_similarity": float(candidate["similarity"]),
            "raw_score": raw_score,
            "normalized_score": normalize_raw_score(raw_score),
        }
        for candidate, raw_score in zip(candidate_set, parsed_scores, strict=True)
    ]
    return sorted(scored, key=lambda candidate: candidate["raw_score"], reverse=True)


def _score_distribution(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": min(values),
        "median": float(median(values)),
        "max": max(values),
    }


def relevance_score_summary(
    queries: Sequence[Mapping[str, Any]],
    reranked_by_query: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Summarize best expected scores and maximum no-related candidate scores."""
    relevant_raw: list[float] = []
    relevant_normalized: list[float] = []
    relevant_not_retrieved = 0
    no_related: list[dict[str, Any]] = []

    for query in queries:
        candidates = reranked_by_query[query["id"]]
        expected_ids = set(query["expected_memory_ids"])
        if expected_ids:
            expected = [
                candidate
                for candidate in candidates
                if candidate["memory_id"] in expected_ids
            ]
            if not expected:
                relevant_not_retrieved += 1
                continue
            best = max(expected, key=lambda candidate: candidate["raw_score"])
            relevant_raw.append(float(best["raw_score"]))
            relevant_normalized.append(float(best["normalized_score"]))
        else:
            if not candidates:
                continue
            best = max(candidates, key=lambda candidate: candidate["raw_score"])
            no_related.append(
                {
                    "query_id": query["id"],
                    "max_raw_score": float(best["raw_score"]),
                    "max_normalized_score": float(best["normalized_score"]),
                    "memory_id": best["memory_id"],
                }
            )

    no_related_raw = [item["max_raw_score"] for item in no_related]
    no_related_normalized = [item["max_normalized_score"] for item in no_related]
    return {
        "relevant_queries": {
            "count": len(relevant_raw),
            "not_retrieved": relevant_not_retrieved,
            "raw": _score_distribution(relevant_raw),
            "normalized": _score_distribution(relevant_normalized),
        },
        "no_related_queries": {
            "per_query": no_related,
            "overall_max_raw_score": max(no_related_raw, default=None),
            "overall_max_normalized_score": max(no_related_normalized, default=None),
        },
    }
