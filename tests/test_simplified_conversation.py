"""Synthetic ablation checks for wiring, facts, safety boundaries and two calls."""

from contextlib import ExitStack
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evolving_companion.affective import (
    AffectiveSnapshot,
    EmotionEvent,
    Mood,
    RelationshipState,
    GROUNDING,
)
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_life import ProjectedLifeContext
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.expression import ExpressionSelector
from evolving_companion.mini_life import MiniLifeService
from evolving_companion.memory_formation import MemoryFormationResult
from evolving_companion.memory_recall import MemoryRecallResult
from evolving_companion.memory_reranker import RerankedMemoryCandidate
from evolving_companion.prompting import Message
from evolving_companion.reply_pipeline import (
    NaturalReplyPipeline,
    Replyer,
    create_reply_pipeline,
)
from evolving_companion.reply_planning import ReplyPlanner, ReplyTarget
from evolving_companion.simplified_conversation import (
    AppraisedTinyPlan,
    ConversationState,
    RelevantContextBuilder,
    TinyPlan,
    TinyPlanner,
)
from evolving_companion.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
SEED = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
CASES = json.loads(
    (ROOT / "tests/fixtures/simplified_conversation_cases.json").read_text(
        encoding="utf-8"
    )
)
NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


class Client:
    def __init__(self, output: str):
        self.output = output
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        return self.output


@pytest.fixture
def character():
    return CharacterProjector().project(SEED)


def state_for(character, case):
    memory = (
        ()
        if "memory" not in case
        else (
            SimpleNamespace(
                memory_type="semantic",
                source=case.get("source", "explicit"),
                content=case["memory"],
            ),
        )
    )
    return ConversationState(
        character,
        [{"role": "user", "content": m} for m in case.get("history", [])],
        ReplyTarget(case["text"]),
        memory,
        mini_life=MiniLifeService(SEED).build(NOW),
    )


def affect_for(case):
    target = uuid4()
    kind = case.get("affect")
    return AffectiveSnapshot.model_validate(
        dict(
            timestamp=NOW,
            relationship=RelationshipState.initial(
                target, NOW, case.get("familiar", True)
            ).model_dump(),
            mood=Mood(
                updated_at=NOW, energy=-0.6 if kind == "low_energy" else 0.05
            ).model_dump(),
            emotions=[]
            if kind not in {"joy", "annoyance", "curiosity"}
            else [
                dict(
                    type=kind,
                    intensity=0.8,
                    target=target,
                    cause_summary="合成感受",
                    created_at=NOW,
                    decay_until=NOW + timedelta(hours=1),
                    source_event_id=uuid4(),
                )
            ],
        )
    )


def plan_for(case):
    return TinyPlan.model_validate(
        dict(
            focus="回应当前合成输入",
            stance=case.get("trait"),
            boundary=case.get("boundary"),
            ask=case["ask"],
        )
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_relevant_context_and_four_field_model(case, character):
    state = state_for(character, case)
    context = RelevantContextBuilder().build(state, affect_for(case))
    assert len(context.traits) <= 3
    if case.get("trait"):
        assert any(case["trait"] in t.content for t in context.traits)
    if "life_relevant" in case:
        assert ("mini_life" in context.facts) == case["life_relevant"]
        if case["life_relevant"]:
            life = context.facts["mini_life"]
            assert isinstance(life, dict)
            assert set(life) == {"now", "next", "today"}
        else:
            assert context.facts["activity"] is None
    client = Client(plan_for(case).model_dump_json())
    planner = TinyPlanner(client)
    plan = planner.plan(planner.build_messages(context, appraise=False), appraise=False)
    assert set(plan.model_dump()) == {"focus", "stance", "boundary", "ask"}
    reply_messages = planner.reply_messages(context, plan)
    system = reply_messages[0]["content"]
    for forbidden in (
        "expression_intent",
        "expression_habits",
        "temporary_style",
        "persona_relevance",
        "reply_reference",
        "prefer",
        "avoid",
        "event_significance",
        "relationship_signal",
        "emotion_impulses",
        "cause_summary",
    ):
        assert forbidden not in system
    assert GROUNDING not in system
    assert "最多3句" in system and "非技术聊天不用 Markdown" in system
    assert "不存在共享现实空间" in system and "romantic=false" in system
    assert reply_messages[-1] == {"role": "user", "content": case["text"]}
    if case["category"] == "memory":
        assert case["memory"] in system and case.get("source", "explicit") in system
        assert "推断不等于事实" in system
    if case.get("affect") == "low_energy":
        assert "主观精力偏低" in system
    elif case.get("affect") == "annoyance":
        assert "烦躁" in system
    elif case.get("affect") == "curiosity":
        assert "好奇" in context.affect
    if case.get("familiar") is False:
        assert "尚不熟悉" in system


def test_tiny_schema_rejects_old_controls_and_invalid_boolean():
    values = dict(focus="回应", stance=None, boundary=None, ask=False)
    for patch in (
        {"tone": "bright"},
        {"ask": "false"},
        {"focus": ""},
        {"stance": "x" * 181},
    ):
        with pytest.raises(ValidationError):
            TinyPlan.model_validate(values | patch)


def test_selector_never_called_or_injected(character, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Simplified must bypass expression control")

    monkeypatch.setattr(ExpressionSelector, "select", forbidden)
    monkeypatch.setattr(ExpressionSelector, "__init__", forbidden)
    monkeypatch.setattr(ExpressionSelector, "temporary_style", forbidden)
    monkeypatch.setattr("evolving_companion.reply_pipeline.condition_intent", forbidden)
    monkeypatch.setattr(ReplyPlanner, "plan", forbidden)
    monkeypatch.setattr(Replyer, "build_messages", forbidden)
    planner, reply = Client(plan_for(CASES[0]).model_dump_json()), Client("合成短回复")
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(reply), simplified=True
    )
    state = state_for(character, CASES[0])
    pipeline.reply([], state.target, character=character, state=state)
    assert len(planner.requests) == len(reply.requests) == 1
    assert pipeline.last_diagnostics is not None
    assert pipeline.last_diagnostics.pipeline == "natural_simplified"
    assert pipeline.last_diagnostics.selected_expression_ids == ()
    assert pipeline.last_diagnostics.temporary_style is None
    assert pipeline.last_relevant_context is not None
    assert pipeline.last_tiny_plan is not None
    assert (
        json.loads(
            TinyPlanner.inspect_reply(
                pipeline.last_relevant_context, pipeline.last_tiny_plan, {}
            )
        )
        == reply.requests[0]
    )


def test_context_is_bounded_and_inspector_redacts(character):
    context = RelevantContextBuilder().build(
        ConversationState(
            character,
            [{"role": "user", "content": "合成内容" * 500} for _ in range(30)],
            ReplyTarget("合成测试密钥 fake-inspector-secret"),
        ),
        None,
    )
    assert len(context.recent) == 6
    assert max(len(m["content"]) for m in context.recent) < 850
    assert all("内容截断" in m["content"] for m in context.recent)
    assert "fake-inspector-secret" not in context.inspect(
        {"DEEPSEEK_API_KEY": "fake-inspector-secret"}
    )
    assert "[REDACTED]" in context.inspect(
        {"DEEPSEEK_API_KEY": "fake-inspector-secret"}
    )
    plan = TinyPlan(
        focus="当前消息", stance="fake-inspector-secret", boundary=None, ask=False
    )
    dump = TinyPlanner.inspect_reply(
        context, plan, {"DEEPSEEK_API_KEY": "fake-inspector-secret"}
    )
    assert "fake-inspector-secret" not in dump
    assert "[REDACTED]" in dump and '"boundary": null' in dump.replace('\\"', '"')


@pytest.mark.parametrize(
    "stage,familiarity,comfort,formality,expected",
    [
        (
            "stranger",
            0.05,
            0.2,
            0.8,
            ("尚不熟悉", "还在了解彼此", "仍有保留", "保持礼貌距离"),
        ),
        (
            "acquainted",
            0.3,
            0.4,
            0.5,
            ("初步认识", "还在了解彼此", "仍有保留", "保持礼貌距离"),
        ),
        ("familiar", 0.75, 0.8, 0.15, ("熟人", "熟悉程度较高", "相处自在", "较少客套")),
        (
            "close",
            0.9,
            0.9,
            0.1,
            ("亲近的熟人", "熟悉程度较高", "相处自在", "较少客套"),
        ),
    ],
)
def test_relation_summary_preserves_social_distance(
    character, stage, familiarity, comfort, formality, expected
):
    affect = affect_for({})
    relationship = RelationshipState.model_validate(
        affect.relationship.model_dump()
        | dict(
            stage=stage, familiarity=familiarity, comfort=comfort, formality=formality
        )
    )
    context = RelevantContextBuilder().build(
        state_for(character, CASES[0]),
        affect.model_copy(update={"relationship": relationship}),
    )
    assert all(label in context.relationship for label in expected)
    assert "romantic=false" in context.relationship
    assert str(relationship.target) not in context.relationship


@pytest.mark.parametrize("recall_fails", [False, True])
def test_simplified_reuses_recall_and_completed_turn_formation(
    tmp_path, character, recall_fails
):
    candidate = RerankedMemoryCandidate(
        str(uuid4()),
        "用户计划购买 Mac。",
        "semantic",
        "explicit",
        "medium",
        NOW.isoformat(),
        0.8,
        2.0,
        0.88,
    )

    class Recall:
        calls = 0

        def recall(
            self,
            current_user_message: str,
            recent_messages: Sequence[Mapping[str, str]],
        ) -> MemoryRecallResult:
            self.calls += 1
            assert current_user_message == "我以前想买什么电脑"
            assert not recent_messages
            if recall_fails:
                raise RuntimeError("private recall detail")
            return MemoryRecallResult(
                True,
                "synthetic",
                (),
                "current_only",
                current_user_message,
                (candidate,),
                (candidate,),
            )

    class Formation:
        calls = 0

        def process_turn(
            self,
            user_archive_message: Mapping[str, str],
            assistant_archive_message: Mapping[str, str],
        ) -> MemoryFormationResult:
            self.calls += 1
            assert core.history[-1]["content"] == assistant_archive_message["content"]
            assert user_archive_message["role"] == "user"
            assert assistant_archive_message["role"] == "assistant"
            return MemoryFormationResult()

    planner, reply = Client(plan_for(CASES[0]).model_dump_json()), Client("合成回复")
    recall, formation = Recall(), Formation()
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(reply), simplified=True
    )
    core = Conversation(
        reply,
        character,
        SQLiteStore(tmp_path / "memory.db"),
        memory_recall_service=recall,
        memory_formation_service=formation,
        reply_pipeline=pipeline,
        clock=FixedClock(NOW),
    )
    assert core.send("我以前想买什么电脑") == "合成回复"
    assert recall.calls == formation.calls == 1
    assert len(planner.requests) == len(reply.requests) == 1
    assert (candidate.content in reply.requests[0][0]["content"]) is not recall_fails
    assert "private recall detail" not in reply.requests[0][0]["content"]
    assert core.last_memory_recall_error == ("RuntimeError" if recall_fails else None)


def test_legacy_retains_single_call_without_mini_life(tmp_path, character, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy must not enter simplified components")

    monkeypatch.setattr(MiniLifeService, "build", forbidden)
    monkeypatch.setattr(RelevantContextBuilder, "build", forbidden)
    client = Client("在。")
    core = Conversation(
        client,
        character,
        SQLiteStore(tmp_path / "legacy.db"),
        mini_life_service=MiniLifeService(SEED),
    )
    assert core.send("在吗") == "在。"
    assert len(client.requests) == 1
    assert core.history[-1]["content"] == "在。"


def test_life_facts_selected_without_inventing_activity(character):
    life = ProjectedLifeContext(
        "student", "学生", "合成住宅", "合成学校", None, "合成住宅"
    )
    state = ConversationState(character, (), ReplyTarget("明天有课吗"), life=life)
    context = RelevantContextBuilder().build(state, None)
    life_facts = context.facts["life"]
    assert isinstance(life_facts, dict)
    assert life_facts["school"] == "合成学校"
    assert (
        context.facts["future_schedule"] is None and context.facts["activity"] is None
    )
    ordinary = RelevantContextBuilder().build(
        ConversationState(character, (), ReplyTarget("你好"), life=life), None
    )
    assert "life" not in ordinary.facts


@pytest.mark.parametrize("failure", [None, "planner", "replyer"])
def test_conversation_completion_affect_and_archive_boundary(
    tmp_path, character, failure
):
    char_id, target = uuid4(), uuid4()
    archive = SQLiteStore(tmp_path / "test.db")
    affect = AffectiveStore(archive.path, char_id, target, NOW)
    before = affect.snapshot(target, NOW)
    combined = AppraisedTinyPlan.model_validate(
        plan_for(CASES[4]).model_dump()
        | dict(
            event_significance=0.1,
            appraisal=dict(
                relevance=0.8,
                valence=0.2,
                novelty=0.1,
                social_meaning="ordinary",
                reality="user_reported",
            ),
            emotion_impulses={"annoyance": 0.4},
            relationship_signal=dict(
                meaningful=False, dimensions=[], direction="none", strength="mild"
            ),
            cause_summary="合成邀请",
            worth_remembering=False,
        )
    )
    planner = Client("invalid" if failure == "planner" else combined.model_dump_json())
    reply = Client("合成回复")
    if failure == "replyer":

        def broken(messages):
            raise RuntimeError("fake provider failure")

        reply.complete = broken
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(reply), affective_store=affect, simplified=True
    )
    core = Conversation(
        reply,
        character,
        archive,
        reply_pipeline=pipeline,
        character_id=char_id,
        character_timezone="UTC",
        clock=FixedClock(NOW),
    )
    if failure == "replyer":
        with pytest.raises(RuntimeError):
            core.send(CASES[4]["text"])
        assert core.history == ()
    else:
        core.send(CASES[4]["text"])
        assert [m["role"] for m in core.history] == ["user", "assistant"]
        final = reply.requests[0][0]["content"]
        assert "不喜欢恐怖内容" in final and "皮蛋" not in final
        assert "romantic=false" in final
    after = affect.snapshot(target, NOW)
    assert after.relationship == before.relationship
    if failure == "planner":
        assert after == before and pipeline.last_diagnostics.planner_error
    else:
        assert after.emotion_strength("annoyance") > 0
    assert len(planner.requests) == 1


@pytest.mark.parametrize(
    "mode", ["natural", "natural_full", "natural_simplified", "legacy"]
)
def test_config_routes_existing_clients_with_cleanup(monkeypatch, mode):
    from evolving_companion import reply_pipeline

    clients = []

    class PlannerClient(Client):
        def __init__(self, **kwargs):
            super().__init__("")
            self.closed = False
            clients.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(reply_pipeline, "LLMClient", PlannerClient)
    with ExitStack() as resources:
        pipeline = create_reply_pipeline(
            resources, Client(""), {"SI_REPLY_PIPELINE": mode}
        )
        if mode == "legacy":
            assert pipeline is None
        else:
            assert pipeline is not None
            assert (pipeline.context_builder is not None) == (
                mode == "natural_simplified"
            )
    assert all(client.closed for client in clients)


def test_no_private_emotion_causes_in_context(character):
    affect = affect_for({"affect": "annoyance"})
    other = EmotionEvent.model_validate(
        dict(
            type="sadness",
            intensity=0.8,
            target=uuid4(),
            cause_summary="另一人的私人经历",
            created_at=NOW,
            decay_until=NOW + timedelta(hours=1),
            source_event_id=uuid4(),
        )
    )
    affect = affect.model_copy(update={"emotions": (*affect.emotions, other)})
    context = RelevantContextBuilder().build(
        ConversationState(character, (), ReplyTarget("你好")), affect
    )
    assert "另一人的私人经历" not in json.dumps(context.payload(), ensure_ascii=False)


def test_mismatched_state_is_rejected_before_llm(character):
    planner = Client("")
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(Client("")), simplified=True
    )
    with pytest.raises(ValueError):
        pipeline.reply(
            [],
            ReplyTarget("甲"),
            state=ConversationState(character, (), ReplyTarget("乙")),
        )
    assert planner.requests == []


def test_long_target_and_recall_are_bounded_without_truncating_current_reply_input(
    character,
):
    target = ReplyTarget("合成消息" * 2000)
    memories = tuple(
        SimpleNamespace(
            memory_type="semantic", source="explicit", content="合成记忆" * 500
        )
        for _ in range(7)
    )
    context = RelevantContextBuilder().build(
        ConversationState(character, (), target, memories), None
    )
    assert len(context.memories) == 3
    assert all(
        len(m["content"]) < 850 and "内容截断" in m["content"] for m in context.memories
    )
    planner = TinyPlanner(Client(""))
    payload = json.loads(planner.build_messages(context, appraise=False)[-1]["content"])
    assert (
        len(payload["target"]["text"]) < 6050
        and "内容截断" in payload["target"]["text"]
    )
    assert (
        planner.reply_messages(context, plan_for(CASES[0]))[-1]["content"]
        == target.text
    )
