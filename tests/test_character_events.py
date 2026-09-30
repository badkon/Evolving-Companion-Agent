import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_events import (
    ActivityEventPayload,
    CharacterEvent,
    CharacterEventService,
    CharacterEventSource,
    CharacterEventType,
)
from evolving_companion.character_state import CharacterStateService
from evolving_companion.character_state_transition import (
    CharacterStateEvent,
    CharacterStateTransitionResult,
    CharacterStateTransitionService,
)
from evolving_companion.storage import SQLiteStore


def _character_id() -> UUID:
    seed_path = Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    return load_character_seed_data(seed_path).identity.internal_id


def _event_service(path: Path) -> tuple[CharacterEventService, CharacterStateService]:
    state_service = CharacterStateService(SQLiteStore(path))
    transition_service = CharacterStateTransitionService(state_service)
    return (
        CharacterEventService(_character_id(), state_service, transition_service),
        state_service,
    )


def _event(
    character_id: UUID,
    event_type: CharacterEventType,
    *,
    activity: str | None = None,
    source: CharacterEventSource = "developer",
) -> CharacterEvent:
    return CharacterEvent(
        character_id=character_id,
        event_type=event_type,
        source=source,
        payload=ActivityEventPayload(activity_name=activity)
        if activity is not None
        else None,
    )


def test_event_ids_are_unique_and_character_key_is_internal_uuid(
    tmp_path: Path,
) -> None:
    key = _character_id()
    first = _event(key, "rest_started")
    second = _event(key, "rest_started")

    assert isinstance(first.event_id, UUID)
    assert first.event_id != second.event_id
    assert first.character_id == key


def test_activity_start_matching_end_and_mismatched_end_safety(
    tmp_path: Path,
) -> None:
    events, state_service = _event_service(tmp_path / "state.db")
    key = _character_id()
    events.handle(_event(key, "activity_started", activity="看漫画"))

    mismatch = events.handle(_event(key, "activity_ended", activity="整理资料"))
    assert not mismatch.handled
    assert mismatch.diagnostics == ("event_not_applied",)
    assert mismatch.before_state is not None
    assert mismatch.before_state.current_activity == "看漫画"
    assert mismatch.after_state == mismatch.before_state
    assert state_service.get_state(key).current_activity == "看漫画"

    ended = events.handle(_event(key, "activity_ended", activity="看漫画"))
    assert ended.handled
    assert ended.after_state is not None
    assert ended.after_state.current_activity is None


def test_activity_end_without_name_is_noop(tmp_path: Path) -> None:
    events, state_service = _event_service(tmp_path / "state.db")
    key = _character_id()
    state_service.update_state(key, current_activity="看漫画")

    result = events.handle(_event(key, "activity_ended"))

    assert not result.handled
    assert result.before_state == result.after_state
    assert "缺少 activity_name" in result.reason


def test_focus_rest_and_end_events_reuse_transition_rules(tmp_path: Path) -> None:
    events, state_service = _event_service(tmp_path / "state.db")
    key = _character_id()
    state_service.update_state(key, energy="low")

    focused = events.handle(_event(key, "focused_task_started", activity="整理资料"))
    assert focused.after_state is not None
    assert focused.after_state.attention == "focused"
    assert focused.after_state.current_activity == "整理资料"

    focus_ended = events.handle(_event(key, "focused_task_ended", activity="整理资料"))
    assert focus_ended.after_state is not None
    assert focus_ended.after_state.attention == "normal"
    assert focus_ended.after_state.current_activity == "整理资料"

    rested = events.handle(_event(key, "rest_started"))
    assert rested.after_state is not None
    assert rested.after_state.energy == "medium"
    ended_rest = events.handle(_event(key, "rest_ended"))
    assert ended_rest.after_state == rested.after_state


def test_session_events_are_noop_state_mappings(tmp_path: Path) -> None:
    events, state_service = _event_service(tmp_path / "state.db")
    key = _character_id()
    before = state_service.get_state(key)

    started = events.handle(_event(key, "conversation_session_started"))
    ended = events.handle(_event(key, "conversation_session_ended"))

    assert started.handled and started.after_state == before
    assert ended.handled and ended.after_state == before


def test_event_validation_and_character_mismatch_do_not_mutate_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    events, _ = _event_service(path)
    key = _character_id()
    with pytest.raises(ValidationError, match="requires activity_name"):
        CharacterEvent(
            character_id=key, event_type="activity_started", source="developer"
        )
    future = CharacterEvent(
        character_id=key,
        event_type="rest_started",
        source="developer",
        occurred_at=datetime.now(timezone.utc) + timedelta(minutes=6),
    )
    assert events.handle(future).diagnostics == ("event_time_too_far_in_future",)

    unknown_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    invalid = events.handle(_event(unknown_id, "rest_started"))
    assert not invalid.handled
    assert invalid.diagnostics == ("character_id_mismatch",)
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM character_state WHERE character_id = ?",
                (str(unknown_id),),
            ).fetchone()[0]
            == 0
        )


def test_event_rollback_uses_a4_1_protection(tmp_path: Path) -> None:
    events, state_service = _event_service(tmp_path / "state.db")
    key = _character_id()
    t1 = datetime.now(timezone.utc) + timedelta(minutes=1)
    original = state_service.get_state(key).model_copy(
        update={"energy": "low", "updated_at": t1}
    )
    state_service.save_state(original)
    past_event = CharacterEvent(
        character_id=key,
        event_type="rest_started",
        source="developer",
        occurred_at=t1 - timedelta(minutes=1),
    )

    result = events.handle(past_event)

    assert not result.handled
    assert result.diagnostics == ("clock_moved_backwards",)
    assert result.after_state == original


def test_events_are_not_persisted_but_state_is(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    events, _ = _event_service(path)
    key = _character_id()
    handled = events.handle(_event(key, "activity_started", activity="看漫画"))
    assert handled.handled

    _, states = _event_service(path)
    assert states.get_state(key).current_activity == "看漫画"
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        memory_count = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert "character_events" not in tables
    assert memory_count == 0


def test_transition_failure_returns_diagnostic_without_state_change(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    state_service = CharacterStateService(SQLiteStore(path))
    key = _character_id()
    before = state_service.get_state(key)

    class FailingTransitionService(CharacterStateTransitionService):
        def apply_event(
            self, character_id: UUID, event: CharacterStateEvent
        ) -> CharacterStateTransitionResult:
            raise RuntimeError("synthetic transition failure")

    events = CharacterEventService(
        key, state_service, FailingTransitionService(state_service)
    )
    result = events.handle(_event(key, "rest_started"))

    assert not result.handled
    assert result.diagnostics == ("transition_failed",)
    assert result.before_state == before
    assert result.after_state == before
