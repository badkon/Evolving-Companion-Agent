from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.character_state import CharacterStateService, Energy
from evolving_companion.character_state_transition import (
    CharacterStateEvent,
    CharacterStateTransitionService,
)
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.prompting import Message
from evolving_companion.storage import SQLiteStore


def _character_id() -> UUID:
    seed = Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    return load_character_seed_data(seed).identity.internal_id


def _services(
    path: Path,
) -> tuple[CharacterStateService, CharacterStateTransitionService]:
    state = CharacterStateService(SQLiteStore(path))
    return state, CharacterStateTransitionService(state)


def _at(hours_ago: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


@pytest.mark.parametrize(
    ("energy", "hours", "expected"),
    [
        ("low", 8, "medium"),
        ("medium", 8, "high"),
        ("high", 8, "high"),
        ("low", 3, "medium"),
        ("medium", 3, "medium"),
        ("low", 2, "low"),
    ],
)
def test_elapsed_energy_rules(
    tmp_path: Path, energy: Energy, hours: int, expected: Energy
) -> None:
    state_service, transitions = _services(tmp_path / "state.db")
    key = _character_id()
    before = state_service.get_state(key)
    state_service.save_state(
        before.model_copy(update={"energy": energy, "updated_at": _at(hours)})
    )

    result = transitions.apply_elapsed_time(key, _at())

    assert result.after_state.energy == expected
    assert result.event_type == "time_elapsed"
    assert result.after_state.updated_at.utcoffset() == timedelta(0)


def test_rest_and_focus_events_are_bounded_and_explainable(tmp_path: Path) -> None:
    state_service, transitions = _services(tmp_path / "state.db")
    key = _character_id()
    state_service.update_state(key, energy="low")

    rested = transitions.apply_event(key, CharacterStateEvent("rest_started"))
    assert rested.after_state.energy == "medium"
    assert rested.changed_fields == ("energy",)
    assert "精力" in rested.reason

    focused = transitions.apply_event(
        key,
        CharacterStateEvent("focused_task_started", activity_name="写作"),
    )
    assert focused.after_state.attention == "focused"
    assert focused.after_state.energy == "medium"
    assert focused.after_state.current_activity == "写作"
    ended = transitions.apply_event(key, CharacterStateEvent("focused_task_ended"))
    assert ended.after_state.attention == "normal"
    assert ended.after_state.current_activity == "写作"


def test_attention_timeout_and_negative_elapsed_diagnostics(tmp_path: Path) -> None:
    state_service, transitions = _services(tmp_path / "state.db")
    key = _character_id()
    before = state_service.get_state(key)
    state_service.save_state(
        before.model_copy(update={"attention": "focused", "updated_at": _at(2)})
    )
    timed_out = transitions.apply_elapsed_time(key, _at())
    assert timed_out.after_state.attention == "normal"

    t1 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    t0 = t1 - timedelta(hours=3)
    original = timed_out.after_state.model_copy(
        update={
            "energy": "low",
            "attention": "focused",
            "mood_tendency": "positive",
            "social_engagement": "engaged",
            "current_activity": "阅读",
            "updated_at": t1,
        }
    )
    state_service.save_state(original)

    backwards = transitions.apply_elapsed_time(key, t0)
    assert backwards.after_state == original
    assert backwards.after_state.updated_at == t1
    assert backwards.changed_fields == ()
    assert backwards.diagnostics == ("clock_moved_backwards",)

    t2 = t1 + timedelta(minutes=30)
    resumed = transitions.apply_elapsed_time(key, t2)
    assert resumed.after_state.energy == "low"
    assert resumed.after_state.attention == "focused"
    assert resumed.after_state.updated_at == t2


def test_conversation_continues_when_system_clock_is_behind(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = SQLiteStore(path)
    state_service = CharacterStateService(store)
    key = _character_id()
    initial = state_service.get_state(key)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    state_service.save_state(
        initial.model_copy(update={"energy": "low", "updated_at": future})
    )

    class ReplyClient(TextCompletionClient):
        def complete(self, messages: list[Message]) -> str:
            assert "精力：偏低" in messages[0]["content"]
            return "可以继续聊。"

    conversation = Conversation(
        ReplyClient(),
        ProjectedCharacterContext("玲"),
        store,
        character_state_service=state_service,
        character_id=key,
    )
    assert conversation.send("你好") == "可以继续聊。"
    after = CharacterStateService(SQLiteStore(path)).get_state(key)
    assert after.energy == "low"
    assert after.updated_at == future


def test_mood_social_and_activity_require_explicit_events(tmp_path: Path) -> None:
    state_service, transitions = _services(tmp_path / "state.db")
    key = _character_id()
    initial = state_service.get_state(key)
    ordinary = transitions.apply_event(
        key, CharacterStateEvent("conversation_turn_completed")
    )
    assert ordinary.after_state.mood_tendency == initial.mood_tendency
    assert ordinary.after_state.social_engagement == initial.social_engagement

    mood = transitions.apply_event(key, CharacterStateEvent("mood_up"))
    assert mood.after_state.mood_tendency == "positive"
    social = transitions.apply_event(key, CharacterStateEvent("social_engagement_up"))
    assert social.after_state.social_engagement == "engaged"
    lowered = transitions.apply_event(
        key, CharacterStateEvent("social_engagement_down")
    )
    assert lowered.after_state.social_engagement == "normal"
    set_activity = transitions.apply_event(
        key, CharacterStateEvent("activity_set", activity_name="看漫画")
    )
    assert set_activity.after_state.current_activity == "看漫画"
    cleared = transitions.apply_event(key, CharacterStateEvent("activity_cleared"))
    assert cleared.after_state.current_activity is None


def test_transition_persists_after_reopening_store(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _, transitions = _services(path)
    key = _character_id()
    transitions.apply_event(key, CharacterStateEvent("mood_up"))

    restarted, _ = _services(path)
    assert restarted.get_state(key).mood_tendency == "positive"


def test_conversation_applies_elapsed_before_prompt_without_semantic_inference(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    store = SQLiteStore(path)
    state_service = CharacterStateService(store)
    key = _character_id()
    initial = state_service.get_state(key)
    state_service.save_state(
        initial.model_copy(update={"energy": "low", "updated_at": _at(9)})
    )

    class InspectingClient(TextCompletionClient):
        def complete(self, messages: list[Message]) -> str:
            assert "精力：适中" in messages[0]["content"]
            return "还好。"

    conversation = Conversation(
        InspectingClient(),
        ProjectedCharacterContext("玲"),
        store,
        character_state_service=state_service,
        character_id=key,
    )
    conversation.send("你今天是不是很累？")
    state = CharacterStateService(SQLiteStore(path)).get_state(key)
    assert state.energy == "medium"
    assert state.mood_tendency == "neutral"
