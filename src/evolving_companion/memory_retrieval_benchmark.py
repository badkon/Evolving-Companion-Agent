"""Pure helpers for the manual memory retrieval benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "memory_retrieval_cases.json"
)


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
