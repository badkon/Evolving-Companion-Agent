"""Run an offline in-memory retrieval comparison with BGE embeddings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "memory_retrieval_cases.json"
MODEL_NAME = "BAAI/bge-base-zh-v1.5"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
TOP_K = 5


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


def print_mode_results(
    name: str,
    queries: list[dict[str, Any]],
    memories_by_id: dict[str, str],
    rankings: dict[str, list[str]],
    scores: dict[str, list[float]],
) -> None:
    print(f"\n=== {name} ===")
    for query in queries:
        query_id = query["id"]
        expected = query["expected_memory_ids"]
        ranking = rankings[query_id]
        score_by_id = dict(zip(ranking, scores[query_id], strict=True))
        rank_by_id = {memory_id: rank for rank, memory_id in enumerate(ranking, 1)}
        print(f"\nQuery ID: {query_id}")
        print(f"Query Text: {query['text']}")
        print(f"Expected Memory IDs: {expected}")
        print("Top-5:")
        for rank, memory_id in enumerate(ranking[:TOP_K], start=1):
            print(
                f"{rank}. {memory_id} | {score_by_id[memory_id]:.4f} | "
                f"{memories_by_id[memory_id]}"
            )
        if expected:
            expected_ranks = [
                f"{memory_id}={rank_by_id.get(memory_id, '>5')}"
                for memory_id in expected
            ]
            print(f"Expected ranks: {', '.join(expected_ranks)}")
        else:
            top_id = ranking[0]
            print(f"No-related Top-1: {top_id} | {score_by_id[top_id]:.4f}")

    metrics = calculate_metrics(queries, rankings)
    print(
        "\nMetrics (queries with expected memories only): "
        f"Recall@1={metrics['recall@1']:.4f}, "
        f"Recall@3={metrics['recall@3']:.4f}, "
        f"Recall@5={metrics['recall@5']:.4f}, MRR={metrics['mrr']:.4f}"
    )


def main() -> None:
    # Keep the embedding package/model out of pytest imports and use CPU by default.
    from sentence_transformers import SentenceTransformer, util

    cases = load_cases()
    memories = cases["memories"]
    queries = cases["queries"]
    memories_by_id = {memory["id"]: memory["content"] for memory in memories}

    print(f"Loading {MODEL_NAME} on CPU; first run may download model files.")
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    memory_embeddings = model.encode(
        [memory["content"] for memory in memories],
        convert_to_tensor=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    mode_results: dict[str, tuple[dict[str, list[str]], dict[str, list[float]]]] = {}
    for mode_name, query_texts in (
        ("Raw query", [query["text"] for query in queries]),
        (
            "Instructed query",
            [f"{QUERY_INSTRUCTION}{query['text']}" for query in queries],
        ),
    ):
        query_embeddings = model.encode(
            query_texts,
            convert_to_tensor=True,
            normalize_embeddings=True,
            show_progress_bar=True,
        )
        similarity_matrix = util.cos_sim(query_embeddings, memory_embeddings)
        rankings: dict[str, list[str]] = {}
        scores: dict[str, list[float]] = {}
        for index, query in enumerate(queries):
            ordered_indices = similarity_matrix[index].argsort(descending=True).tolist()
            ordered_ids = [memories[item_index]["id"] for item_index in ordered_indices]
            ordered_scores = [
                float(similarity_matrix[index][item_index])
                for item_index in ordered_indices
            ]
            rankings[query["id"]] = ordered_ids
            scores[query["id"]] = ordered_scores
        mode_results[mode_name] = (rankings, scores)
        print_mode_results(mode_name, queries, memories_by_id, rankings, scores)

    relevant_count = sum(bool(query["expected_memory_ids"]) for query in queries)
    print("\n=== Summary ===")
    print(f"Memory count: {len(memories)}")
    print(f"Query count: {len(queries)}")
    print(f"Queries with expected memories: {relevant_count}")
    print(f"Queries with no related memory: {len(queries) - relevant_count}")
    for mode_name, (rankings, _) in mode_results.items():
        metrics = calculate_metrics(queries, rankings)
        print(
            f"{mode_name}: Recall@1={metrics['recall@1']:.4f}, "
            f"Recall@3={metrics['recall@3']:.4f}, "
            f"Recall@5={metrics['recall@5']:.4f}, MRR={metrics['mrr']:.4f}"
        )
    print(
        "No-related query Top-1 similarities are listed per query above for both modes."
    )


if __name__ == "__main__":
    main()
