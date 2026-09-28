from evolving_companion.memory_retrieval_benchmark import (
    calculate_metrics,
    load_cases,
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
