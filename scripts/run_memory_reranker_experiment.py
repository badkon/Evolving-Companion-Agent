"""Run a local BGE cross-encoder reranker experiment on the fixed fixture."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.memory_retrieval_benchmark import (
    FIXTURE_PATH,
    calculate_metrics,
    load_cases,
    rank_movement,
)
from evolving_companion.memory_reranker_experiment import (
    relevance_score_summary,
    rerank_candidates,
)
from evolving_companion.storage import SQLiteStore

RERANKER_MODEL = "BAAI/bge-reranker-base"
TOP_N = 10
DISPLAY_TOP_K = 5


def print_semantic_results(candidates: list[dict[str, Any]]) -> None:
    print("  semantic_only Top-10:")
    for rank, candidate in enumerate(candidates, 1):
        print(
            f"    {rank}. {candidate['memory_id']} | "
            f"semantic_similarity={candidate['similarity']:.6f} | "
            f"{candidate['content']}"
        )


def print_reranked_results(candidates: list[dict[str, Any]]) -> None:
    print("  semantic_then_reranker Top-5:")
    for rank, candidate in enumerate(candidates[:DISPLAY_TOP_K], 1):
        print(
            f"    {rank}. {candidate['memory_id']} | "
            f"semantic_similarity={candidate['semantic_similarity']:.6f} | "
            f"raw_score={candidate['raw_score']:.6f} | "
            f"normalized_score={candidate['normalized_score']:.6f} | "
            f"{candidate['content']}"
        )


def print_distribution(name: str, distribution: dict[str, float] | None) -> None:
    if distribution is None:
        print(f"  {name}: no scores")
        return
    print(
        f"  {name}: min={distribution['min']:.6f}, "
        f"median={distribution['median']:.6f}, max={distribution['max']:.6f}"
    )


def main() -> None:
    benchmark_started = perf_counter()
    cases = load_cases(FIXTURE_PATH)
    memories = cases["memories"]
    queries = cases["queries"]

    # Keep experiment data isolated from the default runtime character database.
    with TemporaryDirectory(prefix="si001-cross-encoder-") as temporary_directory:
        store = SQLiteStore(Path(temporary_directory) / "reranker-experiment.db")
        fixture_id_by_store_id: dict[str, str] = {}
        for memory in memories:
            record = store.create_memory(
                "semantic", memory["content"], "explicit", "medium"
            )
            fixture_id_by_store_id[record.id] = memory["id"]

        retriever = MemoryRetriever(store)
        print(f"Fixture: {FIXTURE_PATH}")
        print(f"Reranker model: {RERANKER_MODEL} (CPU)")
        print(f"Semantic candidate boundary: Top-{TOP_N}")
        print("Query text is passed raw; salience and created_at are ignored.")

        load_started = perf_counter()
        from sentence_transformers import CrossEncoder
        from torch.nn import Identity

        cross_encoder = CrossEncoder(RERANKER_MODEL, device="cpu")
        reranker_load_seconds = perf_counter() - load_started

        semantic_rankings: dict[str, list[str]] = {}
        reranked_rankings: dict[str, list[str]] = {}
        reranked_by_query: dict[str, list[dict[str, Any]]] = {}
        rerank_seconds: dict[str, float] = {}
        movement_totals: Counter[str] = Counter(
            {"improved": 0, "unchanged": 0, "worsened": 0, "not_retrieved": 0}
        )

        for query in queries:
            query_id = query["id"]
            semantic_candidates = [
                {
                    **asdict(candidate),
                    "memory_id": fixture_id_by_store_id[candidate.memory_id],
                }
                for candidate in retriever.retrieve(query["text"], top_n=TOP_N)
            ]
            semantic_rankings[query_id] = [
                candidate["memory_id"] for candidate in semantic_candidates
            ]

            pairs = [
                (query["text"], candidate["content"])
                for candidate in semantic_candidates
            ]
            rerank_started = perf_counter()
            raw_scores = cross_encoder.predict(
                pairs,
                activation_fn=Identity(),
                batch_size=TOP_N,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            ranked = rerank_candidates(
                semantic_candidates,
                raw_scores,
                top_n=TOP_N,
            )
            rerank_seconds[query_id] = perf_counter() - rerank_started
            reranked_by_query[query_id] = ranked
            reranked_rankings[query_id] = [
                candidate["memory_id"] for candidate in ranked
            ]
            movement_totals.update(
                rank_movement(
                    semantic_rankings[query_id],
                    reranked_rankings[query_id],
                    query["expected_memory_ids"],
                )
            )

            print(f"\n{'=' * 80}\n{query_id}: {query['text']}")
            print(f"Expected memory IDs: {query['expected_memory_ids']}")
            print_semantic_results(semantic_candidates)
            print_reranked_results(ranked)
            print(f"  Top-10 rerank time: {rerank_seconds[query_id]:.4f}s")
            if query_id == "q_pinn_training":
                before = {
                    memory_id: rank
                    for rank, memory_id in enumerate(semantic_rankings[query_id], 1)
                }
                after = {
                    memory_id: rank
                    for rank, memory_id in enumerate(reranked_rankings[query_id], 1)
                }
                print(
                    "  q_pinn_training expected ranks: "
                    + ", ".join(
                        f"{memory_id}: semantic={before.get(memory_id, '>10')}, "
                        f"reranker={after.get(memory_id, '>10')}"
                        for memory_id in query["expected_memory_ids"]
                    )
                )

        semantic_metrics = calculate_metrics(queries, semantic_rankings)
        reranked_metrics = calculate_metrics(queries, reranked_rankings)
        print("\n=== Overall retrieval metrics ===")
        for name, metrics in (
            ("semantic_only", semantic_metrics),
            ("semantic_then_reranker", reranked_metrics),
        ):
            print(
                f"{name}: Recall@1={metrics['recall@1']:.4f}, "
                f"Recall@3={metrics['recall@3']:.4f}, "
                f"Recall@5={metrics['recall@5']:.4f}, MRR={metrics['mrr']:.4f}"
            )
        print(
            "Expected-memory rank movement: "
            f"improved={movement_totals['improved']}, "
            f"unchanged={movement_totals['unchanged']}, "
            f"worsened={movement_totals['worsened']}, "
            f"not_retrieved={movement_totals['not_retrieved']}"
        )

        score_summary = relevance_score_summary(queries, reranked_by_query)
        relevant = score_summary["relevant_queries"]
        no_related = score_summary["no_related_queries"]
        print("\n=== Relevance score distribution (no threshold selected) ===")
        print(f"Relevant queries with expected candidate: {relevant['count']}")
        print(
            f"Relevant queries without expected candidate in Top-10: {relevant['not_retrieved']}"
        )
        print("Best expected candidate raw score:")
        print_distribution("raw", relevant["raw"])
        print("Best expected candidate normalized score:")
        print_distribution("normalized", relevant["normalized"])
        print("No-related query maximum candidate scores:")
        for item in no_related["per_query"]:
            print(
                f"  {item['query_id']}: memory={item['memory_id']}, "
                f"raw={item['max_raw_score']:.6f}, "
                f"normalized={item['max_normalized_score']:.6f}"
            )
        print(
            "No-related overall maximum: "
            f"raw={no_related['overall_max_raw_score']:.6f}, "
            f"normalized={no_related['overall_max_normalized_score']:.6f}"
        )

        print("\n=== Performance ===")
        print(f"Cross-encoder model load: {reranker_load_seconds:.3f}s")
        for query_id, elapsed in rerank_seconds.items():
            print(f"{query_id} Top-10 rerank: {elapsed:.4f}s")
        print(f"Total benchmark time: {perf_counter() - benchmark_started:.3f}s")
        print("No threshold or preferred mode is selected automatically.")


if __name__ == "__main__":
    main()
