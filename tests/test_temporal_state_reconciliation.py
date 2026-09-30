import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_state import (
    CharacterState,
    CharacterStateService,
    TEMPORAL_ANCHORS,
)
from evolving_companion.character_state_transition import (
    CharacterStateEvent,
    CharacterStateTransitionService,
)
from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore
from evolving_companion.time_model import CharacterTimeService

KEY = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
T0 = datetime(2026, 9, 30, tzinfo=timezone.utc)


def setup_state(
    path: Path, **fields: object
) -> tuple[
    SQLiteStore, FixedClock, CharacterStateService, CharacterStateTransitionService
]:
    store = SQLiteStore(path)
    clock = FixedClock(T0)
    states = CharacterStateService(store, clock)
    state = CharacterState.model_validate(
        {"character_id": KEY, "updated_at": T0, **fields}
    )
    states.save_state(state)
    return store, clock, states, CharacterStateTransitionService(states, clock)


@pytest.mark.parametrize(
    ("field", "value", "hours", "expected"),
    [
        ("energy", "low", 3, "medium"),
        ("energy", "medium", 8, "medium"),
        ("energy", "high", 8, "medium"),
        ("energy", "high", 7, "high"),
        ("attention", "focused", 1, "normal"),
        ("attention", "scattered", 1, "normal"),
        ("mood_tendency", "positive", 6, "neutral"),
        ("mood_tendency", "low", 6, "neutral"),
        ("social_engagement", "engaged", 6, "normal"),
        ("social_engagement", "withdrawn", 6, "normal"),
        ("mood_tendency", "positive", 5, "positive"),
        ("social_engagement", "engaged", 5, "engaged"),
    ],
)
def test_baseline_rules(
    tmp_path: Path, field: str, value: str, hours: int, expected: str
) -> None:
    _, clock, _, transitions = setup_state(tmp_path / "state.db", **{field: value})
    clock.advance(hours=hours)
    result = transitions.apply_elapsed_time(KEY)
    assert getattr(result.after_state, field) == expected
    assert result.after_state.current_activity is None
    if expected == value:
        assert result.after_state == result.before_state
        assert result.changed_fields == ()
    else:
        assert result.changed_fields == (field,)
        assert result.after_state.updated_at == clock.now_utc()
        assert getattr(result.after_state, TEMPORAL_ANCHORS[field]) == clock.now_utc()


def test_checks_accumulate_and_fields_have_independent_anchors(tmp_path: Path) -> None:
    store, clock, states, transitions = setup_state(
        tmp_path / "state.db",
        energy="low",
        attention="focused",
        mood_tendency="positive",
        social_engagement="engaged",
        current_activity="看漫画",
    )
    initial = states.get_state(KEY)
    clock.advance(minutes=30)
    assert transitions.apply_elapsed_time(KEY).after_state == initial
    clock.set(T0 + timedelta(hours=1))
    one_hour = transitions.apply_elapsed_time(KEY).after_state
    assert one_hour.attention == "normal"
    assert one_hour.energy_updated_at == T0
    assert one_hour.mood_updated_at == T0
    clock.set(T0 + timedelta(hours=2))
    assert transitions.apply_elapsed_time(KEY).after_state == one_hour
    clock.set(T0 + timedelta(hours=3))
    three_hours = transitions.apply_elapsed_time(KEY).after_state
    assert three_hours.energy == "medium"
    assert three_hours.mood_tendency == "positive"
    assert three_hours.mood_updated_at == three_hours.social_updated_at == T0
    # Reopen before the six-hour check: independent anchors must be persisted.
    states = CharacterStateService(SQLiteStore(store.path), clock)
    transitions = CharacterStateTransitionService(states, clock)
    clock.set(T0 + timedelta(hours=6))
    six_hours = transitions.apply_elapsed_time(KEY).after_state
    assert six_hours.mood_tendency == "neutral"
    assert six_hours.social_engagement == "normal"
    assert six_hours.energy_updated_at == T0 + timedelta(hours=3)
    assert six_hours.attention_updated_at == T0 + timedelta(hours=1)
    clock.set(T0 + timedelta(hours=24))
    assert transitions.apply_elapsed_time(KEY).after_state.current_activity == "看漫画"


def test_energy_threshold_survives_repeated_no_change_checks(tmp_path: Path) -> None:
    _, clock, states, transitions = setup_state(tmp_path / "state.db", energy="low")
    before = states.get_state(KEY)
    for hours in (1, 2):
        clock.set(T0 + timedelta(hours=hours))
        assert transitions.apply_elapsed_time(KEY).after_state == before
    clock.set(T0 + timedelta(hours=3))
    assert transitions.apply_elapsed_time(KEY).after_state.energy == "medium"


def test_explicit_updates_change_only_affected_anchors(tmp_path: Path) -> None:
    _, clock, states, transitions = setup_state(
        tmp_path / "state.db", mood_tendency="positive"
    )
    clock.advance(hours=2)
    rested = transitions.apply_event(
        KEY, CharacterStateEvent("rest_started")
    ).after_state
    assert rested.energy == "high"
    assert rested.energy_updated_at == clock.now_utc()
    assert rested.mood_updated_at == T0
    clock.advance(hours=1)
    focused = states.update_state(KEY, attention="focused")
    assert focused.attention_updated_at == clock.now_utc()
    assert focused.energy_updated_at == rested.energy_updated_at
    clock.set(T0 + timedelta(hours=6))
    after = transitions.apply_elapsed_time(KEY).after_state
    assert after.mood_tendency == "neutral"
    assert after.energy == "high"
    clock.set(T0 + timedelta(hours=10))
    assert transitions.apply_elapsed_time(KEY).after_state.energy == "medium"


def test_rollback_preserves_every_anchor_and_can_resume(tmp_path: Path) -> None:
    store, clock, states, transitions = setup_state(tmp_path / "state.db", energy="low")
    before = states.get_state(KEY)
    clock.set(T0 - timedelta(hours=4))
    rolled = transitions.apply_elapsed_time(KEY)
    assert rolled.diagnostics == ("clock_moved_backwards",)
    assert rolled.changed_fields == ()
    assert rolled.after_state == before == store.get_character_state(KEY)
    clock.set(T0 + timedelta(hours=3))
    assert transitions.apply_elapsed_time(KEY).after_state.energy == "medium"


def test_offline_duration_uses_a_different_anchor(tmp_path: Path) -> None:
    store, clock, states, transitions = setup_state(tmp_path / "state.db")
    time = CharacterTimeService(store, KEY, "Asia/Shanghai", clock)
    time.record_successful_interaction()
    clock.advance(hours=2)
    states.update_state(KEY, energy="low")
    transitions.apply_event(
        KEY, CharacterStateEvent("focused_task_started", activity_name="写作")
    )
    clock.set(T0 + timedelta(hours=4, minutes=30))
    assert time.snapshot().offline_duration == timedelta(hours=4, minutes=30)
    assert transitions.apply_elapsed_time(KEY).after_state.energy == "low"
    clock.set(T0 + timedelta(hours=5))
    assert time.snapshot().offline_duration == timedelta(hours=5)
    assert transitions.apply_elapsed_time(KEY).after_state.energy == "medium"


def test_conversation_projects_reconciled_state_without_temporal_metadata(
    tmp_path: Path,
) -> None:
    store, clock, states, _ = setup_state(
        tmp_path / "state.db",
        energy="low",
        attention="scattered",
        mood_tendency="low",
        social_engagement="withdrawn",
    )
    clock.advance(hours=6)

    class Client:
        def complete(self, messages: list[Message]) -> str:
            system = messages[0]["content"]
            assert "精力：适中" in system and "注意力：平常" in system
            assert "心境倾向：平稳" in system and "社交投入：平常" in system
            for anchor in TEMPORAL_ANCHORS.values():
                assert anchor not in system
            assert str(KEY) not in system
            return "在呢。"

    conversation = Conversation(
        Client(),
        ProjectedCharacterContext("玲"),
        store,
        character_state_service=states,
        character_id=KEY,
        character_timezone="Asia/Shanghai",
        clock=clock,
    )
    assert conversation.send("在吗？") == "在呢。"
    assert states.get_state(KEY).current_activity is None


def test_legacy_sqlite_state_is_backfilled_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("""CREATE TABLE character_state (
            character_id TEXT PRIMARY KEY, energy TEXT NOT NULL,
            attention TEXT NOT NULL, mood_tendency TEXT NOT NULL,
            social_engagement TEXT NOT NULL, current_activity TEXT, updated_at TEXT NOT NULL)""")
        connection.execute(
            "INSERT INTO character_state VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(KEY),
                "low",
                "focused",
                "positive",
                "engaged",
                "看漫画",
                T0.isoformat(),
            ),
        )
    store = SQLiteStore(path)
    state = store.get_character_state(KEY)
    assert state is not None
    assert state.updated_at == T0 and state.current_activity == "看漫画"
    for anchor in TEMPORAL_ANCHORS.values():
        assert getattr(state, anchor) == T0
    clock = FixedClock(T0 + timedelta(hours=3))
    states = CharacterStateService(store, clock)
    after = (
        CharacterStateTransitionService(states, clock)
        .apply_elapsed_time(KEY)
        .after_state
    )
    assert after.energy == "medium"
    assert after.mood_updated_at == T0
    assert SQLiteStore(path).get_character_state(KEY) == after


@pytest.mark.parametrize("anchor", tuple(TEMPORAL_ANCHORS.values()))
def test_field_anchors_reject_naive_times_and_stay_out_of_prompt(anchor: str) -> None:
    values = {"character_id": KEY, "updated_at": T0, anchor: datetime(2026, 9, 30)}
    with pytest.raises(ValidationError):
        CharacterState.model_validate(values)
    state = CharacterState.model_validate({"character_id": KEY, "updated_at": T0})
    system = PromptBuilder(ProjectedCharacterContext("玲")).build(
        [], "测试", character_state=state
    )[0]["content"]
    assert anchor not in system
