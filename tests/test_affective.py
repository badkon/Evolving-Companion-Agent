from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from random import Random
import sqlite3
from uuid import UUID, uuid4, uuid5

import pytest
from pydantic import ValidationError

from evolving_companion.affective import (
    AffectiveAppraisal,
    BASELINE,
    Event,
    Mood,
    RelationshipState,
    significant_event,
)
from evolving_companion.affective_store import AffectiveStore
from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.expression import ExpressionSelector
from evolving_companion.prompting import PromptBuilder
from evolving_companion.qq_adapter import QQPrivateChatAdapter, relation_target_id
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import (
    AppraisedReplyGuidance,
    ReplyPlanner,
    ReplyTarget,
)
from evolving_companion.storage import SQLiteStore

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
CHAR = UUID("235fe144-519a-4ed0-a36b-670a2c72311f")
USER = relation_target_id(101)


def appraisal(meaning="ordinary", **patch) -> AffectiveAppraisal:
    value = dict(
        event_significance=0.8,
        appraisal=dict(
            relevance=0.9,
            valence=0.5,
            novelty=0.2,
            social_meaning=meaning,
            reality="user_reported",
        ),
        emotion_impulses={"joy": 0.6, "relief": 0.4},
        relationship_signal=dict(
            meaningful=True,
            dimensions=["closeness", "trust", "comfort"],
            direction="positive",
            strength="moderate",
        ),
        cause_summary="合成场景：对方分享了一件重要的事。",
        worth_remembering=False,
    )
    value.update(patch)
    return AffectiveAppraisal.model_validate(value)


def event(at=NOW, actor=USER, id=None):
    return Event(
        id=id or uuid4(),
        type="conversation_message",
        actor=actor,
        target=CHAR,
        summary="合成用户事件",
        timestamp=at,
        source="archive",
    )


@pytest.fixture
def store(tmp_path):
    original = SQLiteStore(tmp_path / "affect.db")
    return AffectiveStore(original.path, CHAR, USER, NOW)


@pytest.mark.parametrize(
    "meaning", ["ordinary", "praise", "achievement", "temporary_change"]
)
def test_ordinary_events_cannot_change_relationship_even_when_llm_requests_it(
    store, meaning
):
    before = store.snapshot(USER, NOW)
    after = store.apply(event(), appraisal(meaning))
    assert store.snapshot(USER, NOW).relationship == before.relationship
    assert after.mood.valence > before.mood.valence
    assert after.mood.valence - before.mood.valence <= 0.04


def test_familiar_bootstrap_is_exact_and_secondary_user_is_not_familiar(store):
    state = store.snapshot(USER, NOW).relationship
    assert state.stage == "familiar"
    assert (
        state.familiarity,
        state.trust,
        state.closeness,
        state.comfort,
        state.formality,
    ) == (0.75, 0.65, 0.65, 0.80, 0.15)
    assert state.romantic is False
    second = store.snapshot(relation_target_id(303), NOW).relationship
    assert second.stage == "stranger"
    assert second.target != state.target


def test_meaningful_relationship_change_is_small_and_visible_next_turn(store):
    before = store.snapshot(USER, NOW)
    current = store.apply(event(), appraisal("personal_disclosure"))
    assert current.relationship == before.relationship
    following = store.snapshot(USER, NOW)
    assert following.relationship.closeness == pytest.approx(0.656)
    assert following.relationship.trust == 0.65  # Not a disclosure -> trust shortcut.
    assert following.relationship.romantic is False


def test_absolute_daily_cap_cannot_be_refunded_by_positive_signals_or_restart(store):
    negative = appraisal(
        "broken_commitment",
        event_significance=0.95,
        relationship_signal=dict(
            meaningful=True,
            dimensions=["trust"],
            direction="negative",
            strength="strong",
        ),
    )
    initial = store.snapshot(USER, NOW).relationship.trust
    store.apply(event(), negative)
    assert store.snapshot(USER, NOW).relationship.trust == pytest.approx(initial - 0.02)
    reopened = AffectiveStore(store.path, CHAR, USER, NOW)
    for _ in range(20):
        reopened.apply(event(), negative)
    assert reopened.snapshot(USER, NOW).relationship.trust == pytest.approx(
        initial - 0.03
    )
    reopened.apply(event(), appraisal("reliable_support"))
    assert reopened.snapshot(USER, NOW).relationship.trust == pytest.approx(
        initial - 0.03
    )
    reopened.apply(event(NOW + timedelta(days=1)), negative)
    assert reopened.snapshot(
        USER, NOW + timedelta(days=1)
    ).relationship.trust == pytest.approx(initial - 0.05)


def test_idempotency_restart_decay_recovery_and_rollback(store):
    source = event()
    first = store.apply(source, appraisal("personal_disclosure"))
    stored = store.snapshot(USER, NOW)
    store.apply(source, appraisal("personal_disclosure"))
    assert store.snapshot(USER, NOW) == stored
    reopened = AffectiveStore(store.path, CHAR, USER, NOW)
    assert reopened.snapshot(USER, NOW) == stored
    later = reopened.snapshot(USER, NOW + timedelta(minutes=30))
    assert 0 < later.emotion_strength("joy") < first.emotion_strength("joy")
    distant = reopened.snapshot(USER, NOW + timedelta(hours=6))
    assert distant.emotions == ()
    assert distant.mood.valence == pytest.approx(
        BASELINE["valence"] + (first.mood.valence - BASELINE["valence"]) / 2
    )
    assert reopened.snapshot(USER, NOW - timedelta(hours=1)).mood == stored.mood


def test_expired_emotions_pruned_and_active_working_set_bounded(store):
    for _ in range(20):
        store.apply(event(), appraisal())
    assert len(store.snapshot(USER, NOW).emotions) <= 24
    store.apply(event(NOW + timedelta(days=1)), appraisal(emotion_impulses={}))
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM emotion_events").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


@pytest.mark.parametrize("reality", ["plan", "hypothetical", "shared_physical_claim"])
def test_reality_gate_blocks_relationship_and_memory_hint(store, reality):
    value = appraisal("broken_commitment").model_dump()
    value["appraisal"]["reality"] = reality
    value["worth_remembering"] = True
    data = AffectiveAppraisal.model_validate(value)
    before = store.snapshot(USER, NOW).relationship
    source = event()
    store.apply(source, data)
    assert store.snapshot(USER, NOW).relationship == before
    assert significant_event(source, data) is None


def test_validation_rejects_nan_out_of_range_romance_and_numeric_relationship_delta():
    with pytest.raises(ValidationError):
        Mood(updated_at=NOW, valence=float("nan"))
    with pytest.raises(ValidationError):
        RelationshipState.model_validate(
            dict(target=USER, updated_at=NOW, romantic=True)
        )
    bad = appraisal().model_dump()
    bad["relationship_signal"]["trust"] = 0.8
    with pytest.raises(ValidationError):
        AffectiveAppraisal.model_validate(bad)


class Client:
    def __init__(self, output):
        self.output = output
        self.requests = []

    def complete(self, messages):
        self.requests.append(messages)
        return self.output


def plan_response(meaning="achievement"):
    return AppraisedReplyGuidance.model_validate(
        dict(
            focus="回应当前输入",
            reply_act="celebrate",
            scene="achievement",
            tone="bright",
            prefer=["celebration"],
            avoid=["curiosity"],
            reply_reference="可以熟人式松口气，不问接下来做什么。",
            **appraisal(meaning).model_dump(),
        )
    )


def test_two_calls_new_emotion_and_old_relationship_reach_replyer(store):
    plan = Client(plan_response("personal_disclosure").model_dump_json())
    reply = Client("合成回复")
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(plan), Replyer(reply), affective_store=store
    )
    context = PromptBuilder(ProjectedCharacterContext("玲；当前具体活动未知")).build(
        [], "代码终于跑通了。"
    )
    assert (
        pipeline.reply(context, ReplyTarget("代码终于跑通了。"), event()) == "合成回复"
    )
    assert len(plan.requests) == len(reply.requests) == 1
    planning = "\n".join(m["content"] for m in plan.requests[0])
    final = "\n".join(m["content"] for m in reply.requests[0])
    assert "较熟悉" in planning and "较熟悉" in final
    assert "开心" in final and "松了口气" in final
    assert "romantic=false" in final and "计划和假设不等于已发生" in final
    assert "现实来家里" in final
    assert "0.656" not in final and str(USER) not in final
    assert pipeline.last_affective_snapshot.relationship.closeness == 0.65
    assert store.snapshot(USER, NOW).relationship.closeness == pytest.approx(0.656)


def test_failed_planner_cannot_mutate_affect(store):
    before = store.snapshot(USER, NOW)
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(Client("invalid")), Replyer(Client("回复")), affective_store=store
    )
    pipeline.reply(
        PromptBuilder(ProjectedCharacterContext("玲")).build([], "你好"),
        ReplyTarget("你好"),
        event(),
    )
    assert store.snapshot(USER, NOW) == before
    assert pipeline.last_diagnostics.planner_error


def test_selector_and_temporary_style_respect_affect(store):
    base = store.snapshot(USER, NOW)
    low = base.model_copy(
        update={"mood": Mood(updated_at=NOW, sociability=-0.6, energy=-0.6)}
    )
    plan = plan_response().expression_intent()
    selector = ExpressionSelector(rng=Random(1))
    assert selector.temporary_style(plan, low) is None
    happy = store.apply(event(), appraisal())
    neutral_score = ExpressionSelector().candidates(plan, "代码", base)[0][1]
    happy_score = ExpressionSelector().candidates(plan, "代码", happy)[0][1]
    assert happy_score > neutral_score


def test_actual_qq_targets_are_isolated_without_raw_account_in_planner(store):
    planner, replyer = Client(plan_response().model_dump_json()), Client("测试")
    core = Conversation(
        replyer,
        ProjectedCharacterContext("玲"),
        SQLiteStore(store.path),
        character_id=CHAR,
        character_timezone="UTC",
        clock=FixedClock(NOW),
        reply_pipeline=NaturalReplyPipeline(
            ReplyPlanner(planner), Replyer(replyer), affective_store=store
        ),
    )
    adapter = QQPrivateChatAdapter(core, allowed_user_ids=[101, 303], bot_user_id=202)
    for index, (user, text) in enumerate(
        [(101, "甲的私聊"), (303, "乙的私聊"), (101, "甲再次说话")]
    ):
        result = adapter.handle_event(
            dict(
                post_type="message",
                message_type="private",
                sub_type="friend",
                user_id=user,
                message_id=index,
                raw_message=text,
            )
        )
        assert result.status == "handled"
    assert "甲的私聊" not in json.dumps(planner.requests[1], ensure_ascii=False)
    assert "乙的私聊" not in json.dumps(planner.requests[2], ensure_ascii=False)
    assert str(USER) not in json.dumps(planner.requests)


def test_local_bootstrap_binds_primary_qq_once_without_resetting_on_reorder(tmp_path):
    path = SQLiteStore(tmp_path / "bootstrap.db").path
    local = uuid5(CHAR, "local-primary")
    AffectiveStore(path, CHAR, local, NOW)
    qq = AffectiveStore(path, CHAR, USER, NOW)
    assert qq.primary_target == USER
    assert qq.snapshot(USER, NOW).relationship.stage == "familiar"
    next_boot = AffectiveStore(path, CHAR, relation_target_id(303), NOW)
    assert next_boot.primary_target == USER
    assert (
        next_boot.snapshot(relation_target_id(303), NOW).relationship.stage
        == "stranger"
    )


def test_significant_event_hint_requires_all_gates():
    source = event()
    assert significant_event(source, appraisal()) is None
    important = appraisal(
        "personal_disclosure", worth_remembering=True, emotion_impulses={"sadness": 0.8}
    )
    assert significant_event(source, important).source_event_id == source.id
    assert (
        significant_event(
            source, appraisal(worth_remembering=True, event_significance=0.1)
        )
        is None
    )


def test_memory_hint_does_not_replace_archive_evidence(tmp_path):
    from evolving_companion.memory_extraction import MemoryExtractor
    from evolving_companion.memory_formation import MemoryFormationService

    store = SQLiteStore(tmp_path / "memory.db")
    conversation_id = str(uuid4())
    user_id = store.append_archive_message(conversation_id, "user", "合成重要事件")
    assistant_id = store.append_archive_message(
        conversation_id, "assistant", "合成回应"
    )
    source = event(id=UUID(user_id))
    hint = significant_event(
        source, appraisal(worth_remembering=True, emotion_impulses={"sadness": 0.8})
    )
    client = Client('{"candidates":[]}')
    formation = MemoryFormationService(MemoryExtractor(client), store)
    result = formation.process_turn_with_affect(
        dict(id=user_id, role="user", content="合成重要事件"),
        dict(id=assistant_id, role="assistant", content="合成回应"),
        hint,
    )
    assert result.saved_memory_ids == () and len(client.requests) == 1
    assert "不是新证据" in client.requests[0][1]["content"]
    assert json.loads(client.requests[0][-1]["content"])[0]["id"] == user_id


def test_greeting_is_small_and_temporary_plan_change_does_not_reduce_trust(store):
    before = store.snapshot(USER, NOW)
    greeting = appraisal(
        event_significance=0.05,
        emotion_impulses={"joy": 0.08},
        relationship_signal=dict(
            meaningful=False, dimensions=[], direction="none", strength="mild"
        ),
    )
    hello = store.apply(event(), greeting)
    assert hello.emotion_strength("joy") < 0.1
    change = appraisal(
        "temporary_change",
        emotion_impulses={"annoyance": 0.2, "disappointment": 0.15},
        relationship_signal=dict(
            meaningful=True,
            dimensions=["trust"],
            direction="negative",
            strength="strong",
        ),
    )
    result = store.apply(event(), change)
    assert result.emotion_strength("annoyance") > 0
    assert result.mood.valence < hello.mood.valence
    assert store.snapshot(USER, NOW).relationship == before.relationship


def test_transaction_failure_rolls_back_receipt_emotion_and_relationship(store):
    before = store.snapshot(USER, NOW)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute(
            "CREATE TRIGGER reject_affect BEFORE UPDATE ON affective_state BEGIN SELECT RAISE(ABORT, 'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.apply(event(), appraisal("personal_disclosure"))
    assert store.snapshot(USER, NOW) == before
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT count(*) FROM affective_receipts").fetchone()[0] == 0


def test_relation_update_is_target_specific_and_survives_multiple_instances(store):
    other = relation_target_id(303)
    secondary = store.snapshot(other, NOW).relationship
    store.apply(event(), appraisal("personal_disclosure"))
    reopened = AffectiveStore(store.path, CHAR, USER, NOW)
    assert reopened.snapshot(other, NOW).relationship == secondary
    assert reopened.snapshot(USER, NOW).relationship.closeness > 0.65


def test_secondary_target_cannot_read_or_write_unowned_primary_memories(store):
    class ForbiddenMemory:
        def recall(self, *args):
            raise AssertionError("Secondary target must not recall primary Memory")

        def process_turn(self, *args):
            raise AssertionError("Secondary target must not form primary Memory")

    replyer = Client("回复")
    core = Conversation(
        replyer,
        ProjectedCharacterContext("玲"),
        SQLiteStore(store.path),
        memory_recall_service=ForbiddenMemory(),
        memory_formation_service=ForbiddenMemory(),
        character_id=CHAR,
        character_timezone="UTC",
        clock=FixedClock(NOW),
        reply_pipeline=NaturalReplyPipeline(
            ReplyPlanner(Client(plan_response().model_dump_json())),
            Replyer(replyer),
            affective_store=store,
        ),
    )
    assert core.send_for("另一个人的内容", relation_target_id(303)) == "回复"
    assert core.last_memory_recall_error is None
    assert core.last_memory_formation_result is None


def test_world_event_boundary_updates_own_affect_not_relationship(store):
    before = store.snapshot(USER, NOW).relationship
    world_event = Event(
        id=uuid4(),
        type="world_event",
        actor=CHAR,
        target=CHAR,
        summary="受信世界事件",
        timestamp=NOW,
        source="world",
    )
    after = store.apply(world_event, appraisal("personal_disclosure"))
    assert after.emotion_strength("joy") > 0
    assert store.snapshot(USER, NOW).relationship == before


def test_reply_failure_keeps_received_event_affect_but_no_completed_history(store):
    class FailingReply:
        def complete(self, messages):
            raise RuntimeError("synthetic failure")

    core = Conversation(
        FailingReply(),
        ProjectedCharacterContext("玲"),
        SQLiteStore(store.path),
        character_id=CHAR,
        character_timezone="UTC",
        clock=FixedClock(NOW),
        reply_pipeline=NaturalReplyPipeline(
            ReplyPlanner(Client(plan_response().model_dump_json())),
            Replyer(FailingReply()),
            affective_store=store,
        ),
    )
    with pytest.raises(RuntimeError):
        core.send("代码终于跑通了")
    assert core.history == ()
    assert store.snapshot(USER, NOW).emotion_strength("joy") > 0
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT role FROM archive_messages").fetchall() == [("user",)]
