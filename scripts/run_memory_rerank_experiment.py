"""Compare semantic retrieval with small salience and recency rerank bonuses."""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.memory_retrieval_benchmark import (
    FIXTURE_PATH,
    calculate_metrics,
    harmful_promotions,
    load_cases,
    rank_movement,
    rerank_top_candidates,
)
from evolving_companion.storage import SQLiteStore

TOP_N = 10
DISPLAY_TOP_K = 5

MODES = (
    ("semantic_only", False, False),
    ("semantic_plus_salience", True, False),
    ("semantic_plus_salience_recency", True, True),
)


def print_candidates(title: str, candidates: list[dict[str, Any]], limit: int) -> None:
    print(f"  {title}:")
    for rank, item in enumerate(candidates[:limit], 1):
        print(
            f"    {rank}. {item['memory_id']} | "
            f"semantic={item['semantic_similarity']:.6f} | "
            f"salience={item['salience']} | age_days={item['age_days']:.1f} | "
            f"salience_bonus={item['salience_bonus']:.4f} | "
            f"recency_bonus={item['recency_bonus']:.4f} | "
            f"final_score={item['final_score']:.6f} | {item['content']}"
        )


def main() -> None:
    cases = load_cases(FIXTURE_PATH)
    memories = cases["memories"]
    queries = cases["queries"]
    now = datetime.now(timezone.utc)

    with TemporaryDirectory(prefix="si001-rerank-") as temporary_directory:
        db_path = Path(temporary_directory) / "rerank-experiment.db"
        store = SQLiteStore(db_path)
        fixture_id_by_store_id: dict[str, str] = {}
        store_id_by_fixture_id: dict[str, str] = {}
        for memory in memories:
            record = store.create_memory(
                "semantic",
                memory["content"],
                "explicit",
                memory["salience"],
            )
            fixture_id_by_store_id[record.id] = memory["id"]
            store_id_by_fixture_id[memory["id"]] = record.id

        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.executemany(
                "UPDATE memories SET created_at = ? WHERE id = ?",
                [
                    (memory["created_at"], store_id_by_fixture_id[memory["id"]])
                    for memory in memories
                ],
            )

        retriever = MemoryRetriever(store)
        all_rankings: dict[str, dict[str, list[str]]] = {
            mode_name: {} for mode_name, _, _ in MODES
        }
        movement_totals = {
            mode_name: Counter(
                {"improved": 0, "unchanged": 0, "worsened": 0, "not_retrieved": 0}
            )
            for mode_name, _, _ in MODES
        }
        harmful_totals = Counter({mode_name: 0 for mode_name, _, _ in MODES})

        print(f"Fixture: {FIXTURE_PATH}")
        print(f"Temporary SQLite database: {db_path}")
        print(f"Semantic candidate Top-N: {TOP_N}")
        print(
            "Bonuses: salience low=0.00, medium=0.02, high=0.04; "
            "recency=0.03 * exp(-age_days / 60)"
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
            results_by_mode: dict[str, list[dict[str, Any]]] = {}
            semantic_ranking = [item["memory_id"] for item in semantic_candidates]

            print(f"\n{'=' * 80}\n{query_id}: {query['text']}")
            print(f"Expected: {query['expected_memory_ids']}")
            semantic_display = rerank_top_candidates(
                semantic_candidates,
                now=now,
                top_n=TOP_N,
                use_salience=False,
                use_recency=False,
            )
            print_candidates("semantic Top-10", semantic_display, TOP_N)

            for mode_name, use_salience, use_recency in MODES:
                ranked = rerank_top_candidates(
                    semantic_candidates,
                    now=now,
                    top_n=TOP_N,
                    use_salience=use_salience,
                    use_recency=use_recency,
                )
                results_by_mode[mode_name] = ranked
                ranking = [item["memory_id"] for item in ranked]
                all_rankings[mode_name][query_id] = ranking
                print_candidates(mode_name + " Top-5", ranked, DISPLAY_TOP_K)
                movement = rank_movement(
                    semantic_ranking,
                    ranking,
                    query["expected_memory_ids"],
                )
                movement_totals[mode_name].update(movement)
                promotions = harmful_promotions(
                    semantic_ranking,
                    ranking,
                    query["expected_memory_ids"],
                    query.get("dangerous_memory_ids", []),
                )
                harmful_totals[mode_name] += len(promotions)
                if promotions:
                    print(f"  Harmful promotions: {promotions}")

            if not query["expected_memory_ids"]:
                print("  No-related query Top-1 / Top-3 by mode:")
                for mode_name, _, _ in MODES:
                    top3 = results_by_mode[mode_name][:3]
                    summary = "; ".join(
                        f"{item['memory_id']} ({item['final_score']:.6f})"
                        for item in top3
                    )
                    print(f"    {mode_name}: {summary}")

            if query_id == "q_pinn_training":
                for mode_name, _, _ in MODES:
                    positions = {
                        item["memory_id"]: rank
                        for rank, item in enumerate(results_by_mode[mode_name], 1)
                    }
                    print(
                        f"  q_pinn_training expected ranks ({mode_name}): "
                        + ", ".join(
                            f"{memory_id}={positions.get(memory_id, '>10')}"
                            for memory_id in query["expected_memory_ids"]
                        )
                    )

        print("\n=== Overall metrics ===")
        for mode_name, _, _ in MODES:
            metrics = calculate_metrics(queries, all_rankings[mode_name])
            movement = movement_totals[mode_name]
            print(
                f"{mode_name}: Recall@1={metrics['recall@1']:.4f}, "
                f"Recall@3={metrics['recall@3']:.4f}, "
                f"Recall@5={metrics['recall@5']:.4f}, MRR={metrics['mrr']:.4f}; "
                f"expected movement improved={movement['improved']}, "
                f"unchanged={movement['unchanged']}, worsened={movement['worsened']}, "
                f"not_retrieved={movement['not_retrieved']}; "
                f"harmful promotion pairs={harmful_totals[mode_name]}"
            )

        print(
            "\nDeclared dangerous memory ids are evaluated only within semantic Top-10."
        )
        print("No mode is selected automatically; compare these measurements manually.")


if __name__ == "__main__":
    main()
