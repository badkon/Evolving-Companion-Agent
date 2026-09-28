"""Pure helpers for the manual memory retrieval benchmark."""

from __future__ import annotations

import json
import math
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from collections.abc import Mapping, Sequence

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "memory_retrieval_cases.json"
)
SALIENCE_BONUSES = {"low": 0.0, "medium": 0.02, "high": 0.04}
MAX_RECENCY_BONUS = 0.03
RECENCY_TAU_DAYS = 60.0


def load_cases(path: Path = FIXTURE_PATH) -> dict[str, list[dict[str, Any]]]:
    """Load and validate the small, checked-in benchmark fixture."""
    data = json.loads(path.read_text(encoding="utf-8"))
    memories = data["memories"]
    queries = data["queries"]
    memory_ids = {memory["id"] for memory in memories}

    if not memories or not queries:
        raise ValueError("Fixture must contain memories and queries")
    for query in queries:
        if not set(query["expected_memory_ids"]).issubset(memory_ids):
            raise ValueError(f"Unknown expected memory id in {query['id']}")
    return data


def calculate_metrics(
    queries: list[dict[str, Any]], rankings: dict[str, list[str]]
) -> dict[str, float]:
    """Calculate per-query averaged Recall@k and reciprocal rank."""
    included = [query for query in queries if query["expected_memory_ids"]]
    if not included:
        return {"recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0, "mrr": 0.0}

    totals = {"recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0, "mrr": 0.0}
    for query in included:
        expected = set(query["expected_memory_ids"])
        ranking = rankings[query["id"]]
        for k in (1, 3, 5):
            totals[f"recall@{k}"] += len(expected.intersection(ranking[:k])) / len(
                expected
            )
        first_relevant_rank = next(
            (
                rank
                for rank, memory_id in enumerate(ranking, start=1)
                if memory_id in expected
            ),
            None,
        )
        if first_relevant_rank is not None:
            totals["mrr"] += 1 / first_relevant_rank

    return {name: value / len(included) for name, value in totals.items()}


def recency_bonus(
    created_at: str,
    now: datetime,
    *,
    max_bonus: float = MAX_RECENCY_BONUS,
    tau_days: float = RECENCY_TAU_DAYS,
) -> tuple[float, float]:
    """Return non-negative age in days and exponentially decayed bonus."""
    if max_bonus < 0 or tau_days <= 0:
        raise ValueError("recency parameters must be non-negative and tau positive")
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - created).total_seconds() / 86400)
    return age_days, max_bonus * math.exp(-age_days / tau_days)


def salience_bonus(salience: str) -> float:
    try:
        return SALIENCE_BONUSES[salience]
    except KeyError as error:
        raise ValueError(f"Unknown salience: {salience}") from error


def rerank_top_candidates(
    semantic_candidates: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    top_n: int = 10,
    use_salience: bool = True,
    use_recency: bool = True,
) -> list[dict[str, Any]]:
    """Rerank only the semantic Top-N candidate boundary using fixed bonuses."""
    if top_n <= 0:
        raise ValueError("top_n must be greater than zero")

    results: list[dict[str, Any]] = []
    for candidate in semantic_candidates[:top_n]:
        salience = candidate["salience"]
        age_days, possible_recency_bonus = recency_bonus(candidate["created_at"], now)
        applied_salience_bonus = salience_bonus(salience) if use_salience else 0.0
        applied_recency_bonus = possible_recency_bonus if use_recency else 0.0
        semantic_similarity = float(candidate["similarity"])
        results.append(
            {
                **candidate,
                "semantic_similarity": semantic_similarity,
                "age_days": age_days,
                "salience_bonus": applied_salience_bonus,
                "recency_bonus": applied_recency_bonus,
                "final_score": (
                    semantic_similarity + applied_salience_bonus + applied_recency_bonus
                ),
            }
        )
    return sorted(results, key=lambda item: item["final_score"], reverse=True)


def rank_movement(
    semantic_ranking: Sequence[str],
    reranked_ranking: Sequence[str],
    expected_memory_ids: Sequence[str],
) -> dict[str, int]:
    """Count expected memories whose rank improves, holds, or gets worse."""
    before = {memory_id: rank for rank, memory_id in enumerate(semantic_ranking, 1)}
    after = {memory_id: rank for rank, memory_id in enumerate(reranked_ranking, 1)}
    movement = {"improved": 0, "unchanged": 0, "worsened": 0, "not_retrieved": 0}
    for memory_id in expected_memory_ids:
        if memory_id not in before or memory_id not in after:
            movement["not_retrieved"] += 1
        elif after[memory_id] < before[memory_id]:
            movement["improved"] += 1
        elif after[memory_id] == before[memory_id]:
            movement["unchanged"] += 1
        else:
            movement["worsened"] += 1
    return movement


def harmful_promotions(
    semantic_ranking: Sequence[str],
    reranked_ranking: Sequence[str],
    expected_memory_ids: Sequence[str],
    dangerous_memory_ids: Sequence[str],
) -> list[tuple[str, str]]:
    """Find declared distractors newly moved above an expected memory."""
    before = {memory_id: rank for rank, memory_id in enumerate(semantic_ranking, 1)}
    after = {memory_id: rank for rank, memory_id in enumerate(reranked_ranking, 1)}
    promotions = []
    for danger_id in dangerous_memory_ids:
        for expected_id in expected_memory_ids:
            if (
                danger_id in before
                and expected_id in before
                and danger_id in after
                and expected_id in after
                and before[danger_id] > before[expected_id]
                and after[danger_id] < after[expected_id]
            ):
                promotions.append((danger_id, expected_id))
    return promotions
