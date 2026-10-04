"""Synthetic structural checks, not claims about real-model semantic quality."""

from datetime import datetime, timezone
import json
from pathlib import Path
from random import Random
from uuid import uuid4

import pytest
from pydantic import ValidationError

from evolving_companion.affective import (
    AffectiveSnapshot,
    EmotionEvent,
    Mood,
    RelationshipState,
)
from evolving_companion.character_data import (
    CharacterSeedData,
    load_character_seed_data,
)
from evolving_companion.character_projection import (
    CharacterProjector,
    select_trait_candidates,
)
from evolving_companion.expression import ExpressionSelector, condition_intent
from evolving_companion.expression_habits import HABITS
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import (
    AppraisedReplyGuidance,
    PersonaRelevance,
    ReplyGuidance,
    ReplyPlanner,
    ReplyTarget,
)
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (ROOT / "tests/fixtures/persona_activation_cases.json").read_text(encoding="utf-8")
)


@pytest.fixture
def character():
    return CharacterProjector().project(
        load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    )


class Client:
    def __init__(self, output: str):
        self.output = output
        self.requests: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.requests.append(messages)
        return self.output


def guidance(ids=(), *, tone="casual", strength=None):
    return ReplyGuidance.model_validate(
        dict(
            focus="回应本轮合成输入",
            reply_act="react",
            scene="ordinary",
            tone=tone,
            prefer=["direct"],
            avoid=["curiosity"],
            reply_reference="表达已提供的立场，不强制追问。",
            persona_relevance=dict(
                active_traits=ids,
                strength=strength or ("high" if ids else "none"),
                reason="合成测试的已知选择。",
            ),
        )
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_seed_candidates_and_planner_selection(case, character):
    history = [{"role": "user", "content": text} for text in case.get("history", [])]
    candidates = select_trait_candidates(
        character, case["text"], history, familiar=case.get("familiar", False)
    )
    assert len(candidates) <= 6
    for expected in case["expected"]:
        assert any(expected in t.content for t in candidates)
    if case.get("empty"):
        assert candidates == ()
    assert case.get("forbidden_id") not in {t.id for t in candidates}
    chosen = () if case.get("choose_none") else tuple(t.id for t in candidates[:3])
    client = Client(guidance(chosen).model_dump_json())
    planner = ReplyPlanner(client)
    messages = planner.build_messages(
        PromptBuilder(character).build(history, case["text"], persona_managed=True),
        ReplyTarget(case["text"]),
        persona_candidates=candidates,
    )
    result = planner.plan(messages, persona_candidates=candidates)
    assert result.persona_relevance.active_traits == chosen
    assert len(client.requests) == 1
    assert "普通聊天助手" in messages[1]["content"]


def test_fixture_covers_all_seventeen_categories():
    assert len(CASES) >= 30
    assert len({case["category"] for case in CASES}) == 17


def test_traits_follow_seed_not_hardcoded_character_preferences():
    data = load_character_seed_data(ROOT / "data/characters/si_001.yaml").model_dump()
    data["seed_preferences"]["dislikes"] = ["合成紫色积木"]
    projected = CharacterProjector().project(CharacterSeedData.model_validate(data))
    assert not any(
        t.category == "dislike"
        for t in select_trait_candidates(projected, "看恐怖片？")
    )
    assert any(
        t.content == "不喜欢合成紫色积木"
        for t in select_trait_candidates(projected, "合成紫色积木要吗？")
    )


@pytest.mark.parametrize(
    "selection",
    [
        {"active_traits": ["a"] * 2, "strength": "high"},
        {"active_traits": ["a", "b", "c", "d"], "strength": "high"},
        {"active_traits": [], "strength": "high"},
        {"active_traits": ["a"], "strength": "none"},
    ],
)
def test_invalid_selection_uses_validation_boundary(selection):
    with pytest.raises(ValidationError):
        PersonaRelevance.model_validate(selection)


@pytest.mark.parametrize("invalid", ["invented_id", None])
def test_invalid_planner_traits_fail_safely_without_retry(character, invalid):
    output = guidance((invalid,) if invalid else ()).model_dump()
    if invalid is None:
        output.pop("persona_relevance")
    planner, reply = Client(json.dumps(output)), Client("合成回复")
    pipeline = NaturalReplyPipeline(ReplyPlanner(planner), Replyer(reply))
    text = "要不要看恐怖片？"
    pipeline.reply(
        PromptBuilder(character).build([], text, persona_managed=True),
        ReplyTarget(text),
        character=character,
    )
    assert len(planner.requests) == len(reply.requests) == 1
    assert pipeline.last_diagnostics.planner_error == "ValueError"
    assert pipeline.last_persona_relevance.active_traits == ()


def test_two_call_flow_only_resolved_traits_reach_replyer(character):
    text = "要不要看恐怖片？"
    candidates = select_trait_candidates(character, text)
    plan = guidance((candidates[0].id,))
    planner, reply = Client(plan.model_dump_json()), Client("合成回复")
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(reply), ExpressionSelector(rng=Random(4))
    )
    messages = PromptBuilder(character).build([], text, persona_managed=True)
    pipeline.reply(messages, ReplyTarget(text), character=character)
    assert len(planner.requests) == len(reply.requests) == 1
    final = "\n".join(m["content"] for m in reply.requests[0])
    assert "不喜欢恐怖内容" in final
    assert "皮蛋" not in final and "香菜" not in final
    assert "Character Voice" in final and "基础表达" in final
    assert "现实见面" in final and "稳定立场优先" in final
    assert len(pipeline.last_diagnostics.selected_expression_ids) <= 1
    assert pipeline.last_diagnostics.temporary_style is None
    assert len(HABITS) == 24
    assert "皮蛋" in PromptBuilder(character).build([], text)[0]["content"]


@pytest.mark.parametrize("kind", ["low_energy", "annoyance", "joy"])
def test_affect_changes_delivery_not_seed_stance(character, kind):
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    target = uuid4()
    mood = Mood(updated_at=now, energy=-0.6 if kind == "low_energy" else 0.05)
    emotion = (
        []
        if kind == "low_energy"
        else [
            EmotionEvent.model_validate(
                dict(
                    type=kind,
                    intensity=0.8,
                    target=target,
                    cause_summary="合成事件",
                    created_at=now,
                    decay_until=now.replace(hour=1),
                    source_event_id=uuid4(),
                )
            )
        ]
    )
    affect = AffectiveSnapshot(
        mood=mood,
        emotions=tuple(emotion),
        relationship=RelationshipState.initial(target, now, True),
        timestamp=now,
    )
    chosen = select_trait_candidates(character, "看恐怖电影吗？", familiar=True)
    intent = condition_intent(
        guidance((chosen[0].id,), tone="bright").expression_intent(), affect
    )
    if kind != "joy":
        assert intent.tone == "quiet"
        assert ExpressionSelector(rng=Random(0)).temporary_style(intent, affect) is None
    assert chosen[0].content == "不喜欢恐怖内容"


def test_none_does_not_force_traits_or_questions(character):
    planner, reply = Client(guidance().model_dump_json()), Client("合成短回复")
    pipeline = NaturalReplyPipeline(ReplyPlanner(planner), Replyer(reply))
    pipeline.reply(
        PromptBuilder(character).build([], "在吗", persona_managed=True),
        ReplyTarget("在吗"),
        character=character,
    )
    assert pipeline.last_persona_relevance.strength == "none"
    assert pipeline.last_trait_candidates == ()
    assert "不喜欢恐怖内容" not in str(reply.requests)


def test_combined_appraisal_persona_conversation_preserves_relationship(
    tmp_path, character
):
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    character_id, user_id = uuid4(), uuid4()
    store = SQLiteStore(tmp_path / "persona.db")
    # SQLiteStore opens/closes a connection per operation; it has no live handle.
    affective = AffectiveStore(store.path, character_id, user_id, now)
    before = affective.snapshot(user_id, now)
    candidate = select_trait_candidates(character, "要不要看恐怖片？")[0]
    combined = AppraisedReplyGuidance.model_validate(
        guidance((candidate.id,)).model_dump()
        | dict(
            event_significance=0.1,
            appraisal=dict(
                relevance=0.4,
                valence=0.4,
                novelty=0.1,
                social_meaning="ordinary",
                reality="user_reported",
            ),
            emotion_impulses={"joy": 0.3},
            relationship_signal=dict(
                meaningful=False, dimensions=[], direction="none", strength="mild"
            ),
            cause_summary="合成邀请",
            worth_remembering=False,
        )
    )
    planner, reply = Client(combined.model_dump_json()), Client("合成结果")
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(planner), Replyer(reply), affective_store=affective
    )
    core = Conversation(
        reply,
        character,
        store,
        reply_pipeline=pipeline,
        character_id=character_id,
        character_timezone="UTC",
        clock=FixedClock(now),
    )
    core.send("要不要看恐怖片？")
    assert len(planner.requests) == len(reply.requests) == 1
    assert pipeline.last_diagnostics is not None
    assert pipeline.last_diagnostics.planner_error is None
    assert pipeline.last_diagnostics.affective_error is None
    assert pipeline.last_persona_relevance.active_traits == (candidate.id,)
    assert affective.snapshot(user_id, now).relationship == before.relationship
    final = "\n".join(m["content"] for m in reply.requests[0])
    assert "不喜欢恐怖内容" in final and "romantic=false" in final
    assert "较熟悉" in final and "皮蛋" not in final
    assert [m["role"] for m in core.history] == ["user", "assistant"]


def test_continuation_retains_stance_but_new_topic_does_not(character):
    history = [
        {"role": "user", "content": "要不要看恐怖片？"},
        {"role": "user", "content": "试一下嘛"},
    ]
    assert any(
        t.content == "不喜欢恐怖内容"
        for t in select_trait_candidates(character, "为什么不？", history)
    )
    assert not any(
        t.category == "dislike"
        for t in select_trait_candidates(character, "那试一下轻音乐？", history)
    )


def test_planner_may_ignore_topical_candidates_for_objective_task(character):
    text = "客观分析恐怖游戏的叙事结构。"
    planner, reply = Client(guidance().model_dump_json()), Client("合成客观回答")
    pipeline = NaturalReplyPipeline(ReplyPlanner(planner), Replyer(reply))
    pipeline.reply(
        PromptBuilder(character).build([], text, persona_managed=True),
        ReplyTarget(text),
        character=character,
    )
    assert pipeline.last_trait_candidates
    assert pipeline.last_persona_relevance.active_traits == ()
    assert "不喜欢恐怖内容" not in str(reply.requests)
