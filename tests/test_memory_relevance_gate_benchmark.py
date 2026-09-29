import json
from dataclasses import dataclass

import pytest

from evolving_companion.memory_relevance_gate_benchmark import (
    build_query,
    confusion_matrix,
    context_delta,
    decide_memory_need,
    diagnostic_tags,
    evaluate_case,
    load_cases,
    select_context_mode,
    score_summary,
)


def test_fixture_coverage():
    data = load_cases()
    cases = data["cases"]
    assert len(cases) == 40
    assert sum(c["expected_gate"] for c in cases) == 20
    contexts = [c for c in cases if c["context_dependent"]]
    assert len(contexts) == 10
    assert {c["expected_gate"] for c in contexts} == {True, False}
    assert {m["memory_type"] for m in data["memories"] if m.get("synthetic_event")} == {
        "relationship",
        "self",
    }


def test_queries_keep_roles_order_and_window():
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
        for i in range(7)
    ]
    assert build_query(messages, "current_only") == "6"
    assert (
        build_query(messages, "recent_context", 3) == "user: 4\nassistant: 5\nuser: 6"
    )
    assert len(build_query(messages, "recent_context").splitlines()) == 5


@pytest.mark.parametrize(
    "messages",
    [
        None,
        [],
        [{}],
        [{"role": "system", "content": "x"}],
        [{"role": "user", "content": " "}],
        [{"role": "assistant", "content": "x"}],
    ],
)
def test_malformed_messages(messages):
    with pytest.raises(ValueError):
        build_query(messages, "current_only")


@pytest.mark.parametrize(
    "mode,window",
    [
        ("unknown", 5),
        ("recent_context", 2),
        ("recent_context", 7),
        ("recent_context", True),
    ],
)
def test_invalid_query_options(mode, window):
    with pytest.raises(ValueError):
        build_query([{"role": "user", "content": "x"}], mode, window)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(cases=[]),
        lambda d: d["cases"][0].update(expected_gate="true"),
        lambda d: d["cases"][0].update(expected_memory_ids=["missing"]),
        lambda d: d["cases"][0].update(expected_gate=False),
        lambda d: d["memories"].append(d["memories"][0]),
        lambda d: d["memories"][0].update(content=""),
        lambda d: d["cases"][0].update(messages=[]),
    ],
)
def test_invalid_fixture(tmp_path, mutation):
    data = load_cases()
    mutation(data)
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


def test_summary_percentiles_and_gap():
    rows = [{"expected_gate": True, "max_score": v} for v in (0.2, 0.4, 0.6, 0.8)]
    rows += [{"expected_gate": False, "max_score": v} for v in (0.1, 0.3)]
    summary = score_summary(rows)
    assert summary["positive"] == pytest.approx(
        {"count": 4, "min": 0.2, "p25": 0.35, "median": 0.5, "p75": 0.65, "max": 0.8}
    )
    assert summary["negative"]["count"] == 2
    assert summary["gap"] == pytest.approx(-0.1)
    assert score_summary([])["gap"] is None
    assert score_summary(rows[:4])["negative"]["count"] == 0


@dataclass
class Candidate:
    memory_id: str
    content: str
    similarity: float


class FakeRetriever:
    def retrieve(self, query, top_n):
        assert query == "user: test"
        assert top_n == 10
        return [Candidate("a", "expected", 0.9), Candidate("b", "wrong", 0.8)]


def test_fake_pipeline_max_is_not_best_expected():
    case = {
        "id": "test",
        "expected_gate": True,
        "expected_memory_ids": ["m1"],
        "messages": [{"role": "user", "content": "test"}],
    }

    def fake_reranker(pairs):
        assert pairs == [("user: test", "expected"), ("user: test", "wrong")]
        return [-8, 0]

    result = evaluate_case(
        case, "recent_context", FakeRetriever(), fake_reranker, {"a": "m1", "b": "m2"}
    )
    assert result["max_score"] == 0.5
    assert result["reranked"][0]["memory_id"] == "m2"
    assert result["semantic"][0]["memory_id"] == "m1"
    assert "recent_context: Wrong-memory high confidence" in diagnostic_tags(
        case, result, result
    )


def test_empty_recall_does_not_become_a_zero_score():
    class EmptyRetriever:
        def retrieve(self, query, top_n):
            return []

    def never_called(pairs):
        pytest.fail("empty recall must not invoke the reranker")

    case = {"id": "empty", "messages": [{"role": "user", "content": "x"}]}
    with pytest.raises(ValueError, match="empty semantic recall"):
        evaluate_case(case, "current_only", EmptyRetriever(), never_called, {})


def test_context_diagnostics_and_delta():
    current = {
        "mode": "current_only",
        "max_score": 0.001,
        "reranked": [{"memory_id": "m1"}],
    }
    recent = {**current, "mode": "recent_context", "max_score": 0.1}
    positive = {"expected_gate": True, "expected_memory_ids": ["m1"]}
    assert context_delta(current, recent) == pytest.approx(0.099)
    tags = diagnostic_tags(positive, current, recent)
    assert "Context rescue" in tags
    assert "current_only: False Negative tendency" in tags
    assert "Context pollution" in diagnostic_tags(positive, recent, current)
    negative = {"expected_gate": False}
    tags = diagnostic_tags(negative, current, recent)
    assert "Context pollution" in tags
    assert "recent_context: False Positive tendency" in tags


def test_memory_need_policy_personal_generic_and_explicit_reset():
    assert decide_memory_need("我的生日是哪天？")["needed"] is True
    assert decide_memory_need("Python 3.14 新增了什么特性？")["needed"] is False
    reset = decide_memory_need("不是，只查学术史：PINN 最早是哪篇论文提出的？")
    assert reset["needed"] is False
    assert reset["rules"] == [
        "topic_reset:不是，只",
        "topic_reset:不是，只查",
        "topic_reset:只查",
        "topic_reset:学术史",
    ]
    assert decide_memory_need("按我的情况，应该选哪一个？")["needed"] is True


def test_selective_context_uses_only_clear_back_references():
    assert select_context_mode("那之前说的计划还合适吗？")["mode"] == "recent_context"
    assert select_context_mode("我的生日是哪天？")["mode"] == "current_only"
    assert (
        select_context_mode("换个话题，火星探测器是什么材料？")["mode"]
        == "current_only"
    )
    assert select_context_mode("那这个呢？")["mode"] == "current_only"
    assert select_context_mode("今天只想知道通用做法")["mode"] == "current_only"


def test_confusion_matrix_counts_all_four_outcomes():
    cases = [
        {"id": "tp", "expected_gate": True},
        {"id": "fn", "expected_gate": True},
        {"id": "fp", "expected_gate": False},
        {"id": "tn", "expected_gate": False},
    ]
    decisions = {
        "tp": {"needed": True},
        "fn": {"needed": False},
        "fp": {"needed": True},
        "tn": {"needed": False},
    }
    assert confusion_matrix(cases, decisions) == {
        "tp": 1,
        "tn": 1,
        "fp": 1,
        "fn": 1,
        "precision": 0.5,
        "recall": 0.5,
        "accuracy": 0.5,
    }


def test_rule_policy_has_explainable_decision_for_each_case():
    cases = load_cases()["cases"]
    decisions = {
        case["id"]: decide_memory_need(case["messages"][-1]["content"])
        for case in cases
    }
    matrix = confusion_matrix(cases, decisions)
    assert matrix["tp"] + matrix["fn"] == 20
    assert matrix["tn"] + matrix["fp"] == 20
    assert all(decision["rules"] for decision in decisions.values())
