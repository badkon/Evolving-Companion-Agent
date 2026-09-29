"""Offline gate diagnostics; no production gate or threshold selection."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import numpy as np

from evolving_companion.memory_reranker_experiment import rerank_candidates
from evolving_companion.memory_policy import (
    MemoryContextPolicy,
    MemoryNeedPolicy,
    build_query as _build_query,
)

MODES = ("current_only", "recent_context")
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests/fixtures/memory_relevance_gate_cases.json"
)
# 人工复查标签的参考值，不是经过校准的概率或生产 gate 阈值。
DIAGNOSTIC_LOW = 0.005
DIAGNOSTIC_HIGH = 0.05
DIAGNOSTIC_DELTA = 0.02


def _text(value: object) -> bool:
    """Validate fixture text fields; separate from the production policy."""
    return isinstance(value, str) and bool(value.strip())


def decide_memory_need(message: str) -> dict:
    decision = MemoryNeedPolicy().evaluate(message)
    return {"needed": decision.needed, "rules": list(decision.matched_rules)}


def select_context_mode(message: str) -> dict:
    decision = MemoryContextPolicy().evaluate(message)
    return {"mode": decision.mode, "rules": list(decision.matched_rules)}


def confusion_matrix(cases: list[dict], decisions: dict[str, dict]) -> dict:
    """Compare experimental rule decisions with fixture labels."""
    counts = {name: 0 for name in ("tp", "tn", "fp", "fn")}
    for case in cases:
        expected = case["expected_gate"]
        predicted = decisions[case["id"]]["needed"]
        key = (
            "tp"
            if expected and predicted
            else "fn"
            if expected
            else "fp"
            if predicted
            else "tn"
        )
        counts[key] += 1
    precision = (
        counts["tp"] / (counts["tp"] + counts["fp"])
        if counts["tp"] + counts["fp"]
        else 0.0
    )
    recall = (
        counts["tp"] / (counts["tp"] + counts["fn"])
        if counts["tp"] + counts["fn"]
        else 0.0
    )
    total = sum(counts.values())
    return {
        **counts,
        "precision": precision,
        "recall": recall,
        "accuracy": (counts["tp"] + counts["tn"]) / total if total else 0.0,
    }


build_query = _build_query


def load_cases(path: Path = FIXTURE_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixture must be an object")
    for key in ("memories", "cases"):
        items = data.get(key)
        if not isinstance(items, list) or not items:
            raise ValueError(f"{key} must be a nonempty list")
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not _text(item.get("id")):
                raise ValueError(f"invalid {key} item")
            if item["id"] in seen:
                raise ValueError(f"duplicate {key} id")
            seen.add(item["id"])
    memory_ids = {memory["id"] for memory in data["memories"]}
    for memory in data["memories"]:
        if not _text(memory.get("content")):
            raise ValueError("memory content must be nonempty")
        if memory.get("memory_type", "semantic") not in (
            "semantic",
            "episodic",
            "self",
            "relationship",
        ):
            raise ValueError("invalid memory_type")
    for case in data["cases"]:
        if type(case.get("expected_gate")) is not bool:
            raise ValueError("expected_gate must be a boolean")
        if type(case.get("context_dependent", False)) is not bool:
            raise ValueError("context_dependent must be a boolean")
        context_mode = case.get("expected_context_mode")
        if case.get("context_dependent") and context_mode not in MODES:
            raise ValueError("context-dependent cases need expected_context_mode")
        if not case.get("context_dependent") and context_mode is not None:
            raise ValueError("non-context case cannot declare expected_context_mode")
        expected = case.get("expected_memory_ids", [])
        if (
            not isinstance(expected, list)
            or any(not _text(item) or item not in memory_ids for item in expected)
            or len(set(expected)) != len(expected)
            or (not case["expected_gate"] and expected)
        ):
            raise ValueError("invalid expected_memory_ids")
        build_query(case.get("messages"), "current_only")
    return data


def evaluate_case(case, mode, retriever, score_pairs, fixture_ids, context_messages=5):
    """Use injected retrieval/scoring calls, so tests never load models."""
    query = build_query(case["messages"], mode, context_messages)
    started = perf_counter()
    retrieved = retriever.retrieve(query, top_n=10)
    semantic_seconds = perf_counter() - started
    semantic = [
        {**asdict(item), "memory_id": fixture_ids[item.memory_id]} for item in retrieved
    ]
    # Empty recall is a pipeline failure, not a measured zero relevance score.
    if not semantic:
        raise ValueError(f"{case['id']}: empty semantic recall; benchmark aborted")
    started = perf_counter()
    scores = score_pairs([(query, item["content"]) for item in semantic])
    ranked = rerank_candidates(semantic, scores)
    rerank_seconds = perf_counter() - started
    return {
        "case_id": case["id"],
        "mode": mode,
        "query": query,
        "expected_gate": case["expected_gate"],
        "semantic": semantic,
        "reranked": ranked,
        "max_score": ranked[0]["normalized_score"],
        "semantic_seconds": semantic_seconds,
        "rerank_seconds": rerank_seconds,
    }


def score_summary(results: list[dict]) -> dict:
    """Linear percentiles of maximum ALL-candidate scores, never expected-only."""
    groups = {}
    for name, expected in (("positive", True), ("negative", False)):
        values = [r["max_score"] for r in results if r["expected_gate"] is expected]
        if any(not np.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError("max_score must be finite and within [0, 1]")
        groups[name] = {"count": len(values)}
        groups[name].update(dict.fromkeys(("min", "p25", "median", "p75", "max")))
        if values:
            groups[name].update(
                zip(
                    ("min", "p25", "median", "p75", "max"),
                    map(
                        float,
                        np.percentile(values, [0, 25, 50, 75, 100], method="linear"),
                    ),
                    strict=True,
                )
            )
    positive_min = groups["positive"]["min"]
    negative_max = groups["negative"]["max"]
    gap = (
        None
        if positive_min is None or negative_max is None
        else positive_min - negative_max
    )
    return {
        **groups,
        "positive_min": positive_min,
        "negative_max": negative_max,
        "gap": gap,
    }


def context_delta(current: dict, recent: dict) -> float:
    return recent["max_score"] - current["max_score"]


def diagnostic_tags(case: dict, current: dict, recent: dict) -> list[str]:
    """Reference-based review hints only; intentionally no predicted_gate output."""
    tags = []
    expected = set(case.get("expected_memory_ids", []))
    for result in (current, recent):
        score = result["max_score"]
        prefix = result["mode"] + ": "
        if not case["expected_gate"] and score >= DIAGNOSTIC_HIGH:
            tags.append(prefix + "False Positive tendency")
        if case["expected_gate"] and score <= DIAGNOSTIC_LOW:
            tags.append(prefix + "False Negative tendency")
        if (
            case["expected_gate"]
            and expected
            and score >= DIAGNOSTIC_HIGH
            and result["reranked"][0]["memory_id"] not in expected
        ):
            tags.append(prefix + "Wrong-memory high confidence")
    delta = context_delta(current, recent)
    if case["expected_gate"]:
        if current["max_score"] <= DIAGNOSTIC_LOW and delta >= DIAGNOSTIC_DELTA:
            tags.append("Context rescue")
        if current["max_score"] >= DIAGNOSTIC_HIGH and delta <= -DIAGNOSTIC_DELTA:
            tags.append("Context pollution")
    elif (
        current["max_score"] <= DIAGNOSTIC_LOW
        and recent["max_score"] >= DIAGNOSTIC_HIGH
    ):
        tags.append("Context pollution")
    return tags
