"""Deterministic routines and simplified wiring; no network or real runtime DB."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import ProjectedLifeContext
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterState
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.mini_life import MiniLifeService
from evolving_companion.prompting import Message
from evolving_companion.reply_pipeline import NaturalReplyPipeline, Replyer
from evolving_companion.reply_planning import ReplyPlanner, ReplyTarget
from evolving_companion.simplified_conversation import (
    ConversationState,
    RelevantContextBuilder,
    TinyPlan,
    TinyPlanner,
)
from evolving_companion.storage import SQLiteStore

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def seed():
    return load_character_seed_data(ROOT / "data/characters/si_001.yaml")


@pytest.mark.parametrize(
    "day,hour,minute,expected",
    [
        (5, 8, 0, "getting_ready"),
        (5, 10, 0, "school"),
        (5, 12, 0, "lunch"),
        (5, 13, 0, "school"),
        (5, 15, 0, "personal"),
        (5, 20, 0, "personal"),
        (5, 22, 0, "preparing_sleep"),
        (5, 23, 30, "sleeping"),
        (4, 8, 0, "getting_ready"),
        (4, 10, 0, "personal"),
    ],
)
def test_schedule(seed, day, hour, minute, expected):
    service = MiniLifeService(seed)
    context = service.build(datetime(2026, 10, day, hour, minute, tzinfo=TZ))
    assert context.now["activity"] in (
        service.free_activities if expected == "personal" else (expected,)
    )
    assert context.now["location"] == ("school" if expected == "school" else "home")
    assert context.today["schedule_type"] == ("weekend" if day == 4 else "weekday")


def test_stability_and_timezone(seed):
    service = MiniLifeService(seed)
    for day in (4, 5, 6, 7):
        now = datetime(2026, 10, day, 20, 10, tzinfo=TZ)
        result = service.build(now)
        assert result == service.build(now.replace(minute=59))
        assert result == MiniLifeService(seed).build(now.astimezone(timezone.utc))
    with pytest.raises(ValueError, match="aware"):
        service.build(datetime(2026, 10, 5, 20))


def test_tomorrow_and_seed_constraints(seed):
    service = MiniLifeService(seed)
    sunday = service.build(datetime(2026, 10, 4, 23, 30, tzinfo=TZ))
    assert "学校" in sunday.today["tomorrow_schedule"]
    assert sunday.next["time_hint"] == "明天 07:00左右"
    friday = service.build(datetime(2026, 10, 9, 20, tzinfo=TZ))
    assert friday.today["tomorrow_schedule"] == "以自由时间为主"
    data = seed.model_dump()
    data["seed_preferences"]["likes"] = []
    data["initial_life_context"]["life_stage"] = "unknown"
    data["initial_life_context"]["home_entity_id"] = None
    service = MiniLifeService(type(seed).model_validate(data))
    result = service.build(datetime(2026, 10, 5, 10, tzinfo=TZ))
    assert result.now == {"activity": "relaxing", "location": "unknown"}
    # Existing Life State overrides seed's initial student association.
    assert (
        MiniLifeService(seed)
        .build(
            datetime(2026, 10, 5, 10, tzinfo=TZ),
            ProjectedLifeContext("worker", None, "家", None, None, None),
        )
        .now["activity"]
        != "school"
    )


def test_compact_injection_and_grounding(seed):
    mini = MiniLifeService(seed).build(datetime(2026, 10, 5, 20, tzinfo=TZ))
    state = ConversationState(
        CharacterProjector().project(seed),
        (),
        ReplyTarget("现在在干嘛"),
        mini_life=mini,
    )
    context = RelevantContextBuilder().build(state, None)
    assert set(context.facts["mini_life"]) == {"now", "next", "today"}
    assert context.facts["shared_physical_interface"] is False
    assert context.facts["live_user_camera"] is False
    assert "不是过去活动记录" in context.facts["life_basis"]
    assert "schedule" not in context.facts["mini_life"]
    plan = TinyPlan(focus="当前活动", stance=None, boundary=None, ask=False)
    system = TinyPlanner.reply_messages(context, plan)[0]["content"]
    assert "不是过去经历或具体课表" in system
    assert "最多3句" in system
    assert "不存在共享现实空间" in system


def test_explicit_state_is_not_replaced(seed):
    now = datetime(2026, 10, 5, 20, tzinfo=TZ)
    explicit = CharacterState.model_validate(
        dict(
            character_id=seed.identity.internal_id,
            updated_at=now,
            current_activity="整理资料",
        )
    )
    context = RelevantContextBuilder().build(
        ConversationState(
            CharacterProjector().project(seed),
            (),
            ReplyTarget("现在在干嘛"),
            character_state=explicit,
            mini_life=MiniLifeService(seed).build(now),
        ),
        None,
    )
    assert context.facts["activity"] == "整理资料"
    assert "显式状态与观察优先" in context.facts["life_basis"]


class FakeClient:
    def __init__(self, output: str):
        self.output = output

    def complete(self, messages: list[Message]) -> str:
        return self.output


@pytest.mark.parametrize("simplified", [False, True])
def test_conversation_only_injects_simplified(seed, tmp_path, simplified, monkeypatch):
    pipeline = NaturalReplyPipeline(
        ReplyPlanner(
            FakeClient('{"focus":"活动","stance":null,"boundary":null,"ask":false}')
        ),
        Replyer(FakeClient("合成回复")),
        simplified=simplified,
    )
    store = SQLiteStore(tmp_path / "mini.db")
    service = MiniLifeService(seed)
    calls = []
    original = service.build

    def build(now, life=None):
        calls.append(now)
        return original(now, life)

    monkeypatch.setattr(service, "build", build)
    # SQLiteStore closes each operation's connection; it holds no open handle.
    conversation = Conversation(
        FakeClient("合成回复"),
        CharacterProjector().project(seed),
        store,
        clock=FixedClock(datetime(2026, 10, 5, 20, tzinfo=TZ)),
        mini_life_service=service,
        reply_pipeline=pipeline,
    )
    assert conversation.send("现在在干嘛") == "合成回复"
    assert len(calls) == int(simplified)
    context = pipeline.last_relevant_context
    if simplified:
        assert context is not None
        assert "mini_life" in context.facts
    else:
        assert context is None
