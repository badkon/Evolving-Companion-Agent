from datetime import datetime, timedelta, timezone

import pytest

from evolving_companion.memory_retrieval_benchmark import (
    calculate_metrics,
    harmful_promotions,
    load_cases,
    rank_movement,
    recency_bonus,
    rerank_top_candidates,
    salience_bonus,
)


def test_retrieval_fixture_has_valid_ids_and_expected_size() -> None:
    cases = load_cases()
    memories = cases["memories"]
    queries = cases["queries"]
    memory_ids = {memory["id"] for memory in memories}

    assert 30 <= len(memories) <= 50
    assert 15 <= len(queries) <= 20
    assert len(memory_ids) == len(memories)
    assert any(not query["expected_memory_ids"] for query in queries)
    assert all(
        set(query["expected_memory_ids"]).issubset(memory_ids) for query in queries
    )
    assert all(memory["salience"] in {"low", "medium", "high"} for memory in memories)
    assert all(datetime.fromisoformat(memory["created_at"]) for memory in memories)


def test_metrics_average_recall_and_mrr_excluding_no_match_queries() -> None:
    queries = [
        {"id": "q1", "expected_memory_ids": ["a", "b"]},
        {"id": "q2", "expected_memory_ids": ["z"]},
        {"id": "q3", "expected_memory_ids": []},
    ]
    rankings = {"q1": ["a", "x", "b"], "q2": ["x", "y", "z"], "q3": ["x"]}

    metrics = calculate_metrics(queries, rankings)

    assert metrics == {
        "recall@1": 0.25,
        "recall@3": 1.0,
        "recall@5": 1.0,
        "mrr": 2 / 3,
    }


def test_salience_bonuses_are_small_and_fixed() -> None:
    assert salience_bonus("low") == 0.0
    assert salience_bonus("medium") == 0.02
    assert salience_bonus("high") == 0.04


def test_recency_bonus_decays_exponentially_and_clamps_future_age() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)

    age_days, bonus = recency_bonus((now - timedelta(days=60)).isoformat(), now)
    future_age, future_bonus = recency_bonus(
        (now + timedelta(days=10)).isoformat(), now
    )

    assert age_days == 60
    assert future_age == 0
    assert bonus == pytest.approx(0.03 * 2.718281828459045**-1)
    assert future_bonus == 0.03


def test_rerank_only_reorders_semantic_top_n_candidates() -> None:
    candidates = [
        {
            "memory_id": "semantic-top-1",
            "content": "strong semantic match",
            "similarity": 0.80,
            "salience": "low",
            "created_at": "2026-07-30T00:00:00+00:00",
        },
        {
            "memory_id": "semantic-top-2",
            "content": "slightly weaker match",
            "similarity": 0.79,
            "salience": "high",
            "created_at": "2026-09-28T00:00:00+00:00",
        },
        {
            "memory_id": "outside-candidate-set",
            "content": "must not be considered",
            "similarity": 0.99,
            "salience": "high",
            "created_at": "2026-09-28T00:00:00+00:00",
        },
    ]

    ranked = rerank_top_candidates(
        candidates,
        now=datetime(2026, 9, 28, tzinfo=timezone.utc),
        top_n=2,
    )

    assert [item["memory_id"] for item in ranked] == [
        "semantic-top-2",
        "semantic-top-1",
    ]
    assert "outside-candidate-set" not in {item["memory_id"] for item in ranked}
    assert ranked[0]["salience_bonus"] == 0.04
    assert ranked[0]["recency_bonus"] == 0.03


def test_rank_movement_and_dangerous_promotions() -> None:
    semantic = ["expected-a", "expected-b", "danger", "other"]
    reranked = ["expected-b", "danger", "expected-a", "other"]

    movement = rank_movement(
        semantic, reranked, ["expected-a", "expected-b", "missing"]
    )
    promotions = harmful_promotions(
        semantic,
        reranked,
        ["expected-a", "expected-b"],
        ["danger"],
    )

    assert movement == {
        "improved": 1,
        "unchanged": 0,
        "worsened": 1,
        "not_retrieved": 1,
    }
    assert promotions == [("danger", "expected-a")]
