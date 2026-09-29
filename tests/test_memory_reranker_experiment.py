from evolving_companion.memory_reranker_experiment import (
    normalize_raw_score,
    parse_raw_scores,
    relevance_score_summary,
    rerank_candidates,
)


def candidate(memory_id: str, similarity: float = 0.5) -> dict[str, object]:
    return {
        "memory_id": memory_id,
        "content": f"content for {memory_id}",
        "similarity": similarity,
    }


def test_parse_scores_accepts_scalar_and_single_column_outputs() -> None:
    assert parse_raw_scores([1.2, -0.5], 2) == [1.2, -0.5]
    assert parse_raw_scores([[1.2], [-0.5]], 2) == [1.2, -0.5]


def test_normalization_is_stable_for_positive_and_negative_logits() -> None:
    assert normalize_raw_score(0.0) == 0.5
    assert normalize_raw_score(1000.0) == 1.0
    assert normalize_raw_score(-1000.0) == 0.0


def test_reranker_sorts_only_the_semantic_top_ten() -> None:
    candidates = [candidate(f"m{index}", 1 - index / 100) for index in range(11)]
    scores = list(range(10))

    results = rerank_candidates(candidates, scores)

    assert len(results) == 10
    assert results[0]["memory_id"] == "m9"
    assert results[-1]["memory_id"] == "m0"
    assert "m10" not in {item["memory_id"] for item in results}
    assert results[0]["raw_score"] == 9.0
    assert results[0]["normalized_score"] == normalize_raw_score(9.0)


def test_relevance_summary_compares_expected_and_no_related_scores() -> None:
    queries = [
        {"id": "relevant", "expected_memory_ids": ["expected-a", "expected-b"]},
        {"id": "missed", "expected_memory_ids": ["not-in-top10"]},
        {"id": "unrelated", "expected_memory_ids": []},
    ]
    ranked = {
        "relevant": [
            {"memory_id": "expected-a", "raw_score": 2.0, "normalized_score": 0.8},
            {"memory_id": "expected-b", "raw_score": 3.0, "normalized_score": 0.9},
            {"memory_id": "other", "raw_score": -1.0, "normalized_score": 0.2},
        ],
        "missed": [{"memory_id": "other", "raw_score": 0.5, "normalized_score": 0.6}],
        "unrelated": [
            {"memory_id": "weak-a", "raw_score": -0.5, "normalized_score": 0.4},
            {"memory_id": "weak-b", "raw_score": -0.8, "normalized_score": 0.3},
        ],
    }

    summary = relevance_score_summary(queries, ranked)

    assert summary["relevant_queries"] == {
        "count": 1,
        "not_retrieved": 1,
        "raw": {"min": 3.0, "median": 3.0, "max": 3.0},
        "normalized": {"min": 0.9, "median": 0.9, "max": 0.9},
    }
    assert summary["no_related_queries"]["per_query"] == [
        {
            "query_id": "unrelated",
            "max_raw_score": -0.5,
            "max_normalized_score": 0.4,
            "memory_id": "weak-a",
        }
    ]
    assert summary["no_related_queries"]["overall_max_raw_score"] == -0.5
    assert summary["no_related_queries"]["overall_max_normalized_score"] == 0.4
