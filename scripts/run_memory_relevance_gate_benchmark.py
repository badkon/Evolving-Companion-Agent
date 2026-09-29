"""Manual CPU-only relevance gate experiment; uses only a temporary database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter

from evolving_companion.embeddings import MODEL_NAME, EmbeddingService
from evolving_companion.memory_relevance_gate_benchmark import (
    DIAGNOSTIC_DELTA,
    DIAGNOSTIC_HIGH,
    DIAGNOSTIC_LOW,
    FIXTURE_PATH,
    MODES,
    context_delta,
    confusion_matrix,
    decide_memory_need,
    diagnostic_tags,
    evaluate_case,
    load_cases,
    select_context_mode,
    score_summary,
)
from evolving_companion.memory_retrieval import MemoryRetriever
from evolving_companion.storage import SQLiteStore


def print_result(case, result):
    print(f"\n{case['id']} | expected_gate={case['expected_gate']} | {result['mode']}")
    print(f"Constructed query:\n{result['query']}")
    for key in ("semantic", "reranked"):
        print(f"{key} Top-3:")
        for rank, item in enumerate(result[key][:3], 1):
            score = f"semantic={item['similarity']:.6f}"
            if key == "reranked":
                score += (
                    f" | raw={item['raw_score']:.6f}"
                    f" | sigmoid={item['normalized_score']:.6f}"
                )
            print(f"  {rank}. {item['memory_id']} | {score} | {item['content']}")
    best = result["reranked"][0]
    print(
        f"max_score={result['max_score']:.6f} | {best['memory_id']} | {best['content']}"
    )
    for memory_id in case.get("expected_memory_ids", []):
        ranks = {}
        for key in ("semantic", "reranked"):
            order = [item["memory_id"] for item in result[key]]
            ranks[key] = order.index(memory_id) + 1 if memory_id in order else ">10"
        print(f"Expected {memory_id}: {ranks}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-messages", type=int, choices=range(3, 7), default=5)
    args = parser.parse_args()
    started = perf_counter()
    fixture = load_cases()
    print(f"Fixture: {FIXTURE_PATH}")
    print(f"Memories={len(fixture['memories'])}, cases={len(fixture['cases'])}")
    print(f"CPU | embedding={MODEL_NAME} | reranker=BAAI/bge-reranker-base")
    print(f"Recent window={args.context_messages}; raw queries; semantic Top-10.")
    print(
        "Sigmoid scores are not calibrated probabilities. No gate prediction/threshold selection."
    )
    print(
        f"Review references: low<={DIAGNOSTIC_LOW}, high>={DIAGNOSTIC_HIGH}, "
        f"significant delta>={DIAGNOSTIC_DELTA} in magnitude."
    )
    print(
        "FP: negative/high; FN: positive/low; wrong-memory: positive/high/nonexpected top."
    )
    print(
        "Rescue: positive low current + rising delta; pollution: positive high current "
        "+ falling delta, or negative low current -> high context. Tags are review hints only."
    )
    print(
        "Use a fresh process: load timings include actual model initialization, not cache lookup alone."
    )
    print(
        "First download, if needed, is included; subsequent runs measure disk-cache cold start."
    )

    load_started = perf_counter()
    embedding = EmbeddingService()
    # Experimental timing hook only; do not change the production lazy-loading API.
    embedding._load_model()
    embedding_load = perf_counter() - load_started
    load_started = perf_counter()
    from sentence_transformers import CrossEncoder
    from torch.nn import Identity

    encoder = CrossEncoder("BAAI/bge-reranker-base", device="cpu")
    reranker_load = perf_counter() - load_started

    def score_pairs(pairs):
        return encoder.predict(
            pairs,
            activation_fn=Identity(),
            batch_size=10,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

    results = {mode: [] for mode in MODES}
    policy_decisions = {}
    context_decisions = {}
    policy_seconds = 0.0
    context_rows = []
    with TemporaryDirectory(prefix="si001-gate-benchmark-") as directory:
        db_path = Path(directory) / "gate.db"
        print(f"Temporary DB: {db_path} (removed on exit)")
        store = SQLiteStore(db_path)
        fixture_ids = {}
        for memory in fixture["memories"]:
            record = store.create_memory(
                memory.get("memory_type", "semantic"),
                memory["content"],
                "explicit",
                "medium",
            )
            fixture_ids[record.id] = memory["id"]
            if "created_at" in memory:
                # Preserve old-but-valid facts in this disposable DB only.
                with closing(sqlite3.connect(db_path)) as connection, connection:
                    connection.execute(
                        "UPDATE memories SET created_at=? WHERE id=?",
                        (memory["created_at"], record.id),
                    )
        retriever = MemoryRetriever(store, embedding)
        for case in fixture["cases"]:
            current_message = case["messages"][-1]["content"]
            policy_started = perf_counter()
            need = decide_memory_need(current_message)
            context_choice = select_context_mode(current_message)
            policy_seconds += perf_counter() - policy_started
            policy_decisions[case["id"]] = need
            context_decisions[case["id"]] = context_choice
            pair = []
            for mode in MODES:
                result = evaluate_case(
                    case,
                    mode,
                    retriever,
                    score_pairs,
                    fixture_ids,
                    args.context_messages,
                )
                results[mode].append(result)
                pair.append(result)
                print_result(case, result)
            if case.get("context_dependent"):
                context_correct = (
                    context_choice["mode"] == case["expected_context_mode"]
                )
                context_rows.append(context_correct)
                print(
                    f"Context comparison: current_only={pair[0]['max_score']:.6f}, "
                    f"recent_context={pair[1]['max_score']:.6f}, "
                    f"score_delta={context_delta(*pair):+.6f}"
                )
                print(
                    f"Context policy: chosen={context_choice['mode']} | "
                    f"expected={case['expected_context_mode']} | "
                    f"{'correct' if context_correct else 'incorrect'} | "
                    f"rules={context_choice['rules']}"
                )
            selected_mode = context_choice["mode"] if need["needed"] else None
            print(
                f"Memory Need: {'true' if need['needed'] else 'false'} | rules={need['rules']}"
            )
            if selected_mode is None:
                print("rule_based_selective: NO_MEMORY (retrieval result not adopted)")
            else:
                selected = pair[MODES.index(selected_mode)]
                print(f"rule_based_selective: selected={selected_mode}")
                print_result(case, selected)
            print(f"Review tags: {diagnostic_tags(case, *pair) or 'none'}")

    matrix = confusion_matrix(fixture["cases"], policy_decisions)
    print("\n=== Memory Need Policy ===")
    print(json.dumps(matrix, ensure_ascii=False, indent=2))
    print("False Positive cases:")
    false_positives = [
        c
        for c in fixture["cases"]
        if not c["expected_gate"] and policy_decisions[c["id"]]["needed"]
    ]
    print(
        "  none"
        if not false_positives
        else "\n".join(
            f"  {c['id']}: {policy_decisions[c['id']]['rules']}"
            for c in false_positives
        )
    )
    print("False Negative cases:")
    false_negatives = [
        c
        for c in fixture["cases"]
        if c["expected_gate"] and not policy_decisions[c["id"]]["needed"]
    ]
    print(
        "  none"
        if not false_negatives
        else "\n".join(
            f"  {c['id']}: {policy_decisions[c['id']]['rules']}"
            for c in false_negatives
        )
    )
    context_total = len(context_rows)
    print("\n=== Selective Context Policy ===")
    print(f"Context-dependent cases correct: {sum(context_rows)}/{context_total}")
    print(
        "rule_based_selective adopts no retrieval for Memory Need=false; "
        "positive cases use the selected current_only/recent_context baseline result."
    )
    print(f"Policy evaluation total: {policy_seconds:.6f}s")
    if false_positives or false_negatives:
        print(
            "Review note: errors are fixture diagnostics; no automatic rule tuning was applied."
        )

    print("\n=== Maximum ALL-candidate score distributions ===")
    print(
        "Baselines: current_only_everywhere and recent_context_everywhere retrieve for every case."
    )
    for mode, rows in results.items():
        strategy = f"{mode}_everywhere"
        summary = score_summary(rows)
        print(f"{strategy}: {json.dumps(summary, ensure_ascii=False, indent=2)}")
        gap = summary["gap"]
        if gap is None:
            print("Gap unavailable: a label group is empty.")
        elif gap < 0:
            print("Score ranges overlap.")
        elif gap > 0:
            print("Complete separation on this fixture only; no threshold selected.")
        else:
            print("Score ranges touch.")
        print(
            f"Average semantic retrieval: {mean(r['semantic_seconds'] for r in rows):.4f}s"
        )
        print(f"Average Top-10 rerank: {mean(r['rerank_seconds'] for r in rows):.4f}s")
    all_results = [row for rows in results.values() for row in rows]
    print(f"Embedding model load: {embedding_load:.3f}s")
    print(f"Reranker model load: {reranker_load:.3f}s")
    print(
        f"Average semantic retrieval: {mean(r['semantic_seconds'] for r in all_results):.4f}s"
    )
    print(
        f"Average Top-10 rerank: {mean(r['rerank_seconds'] for r in all_results):.4f}s"
    )
    print(
        "First semantic call includes populating the temporary embedding cache; later calls reuse it."
    )
    print(f"Total benchmark: {perf_counter() - started:.3f}s")


if __name__ == "__main__":
    main()
