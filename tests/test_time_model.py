from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evolving_companion.clock import FixedClock, SystemClock
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.conversation import Conversation
from evolving_companion.character_state import CharacterStateService
from evolving_companion.character_state_transition import (
    CharacterStateTransitionService,
)
from evolving_companion.prompting import Message
from evolving_companion.storage import SQLiteStore
from evolving_companion.time_model import CharacterTimeService, format_offline_duration


def _seed():
    return load_character_seed_data(
        Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    )


def test_clocks_are_aware_utc_and_fixed_clock_advances() -> None:
    fixed = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    fixed.advance(hours=8)
    assert fixed.now_utc() == datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    assert fixed.now_utc().utcoffset() == timedelta(0)
    assert SystemClock().now_utc().utcoffset() == timedelta(0)


def test_snapshot_timezone_persistence_and_rollback(tmp_path: Path) -> None:
    seed = _seed()
    key = seed.identity.internal_id
    store = SQLiteStore(tmp_path / "time.db")
    clock = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    service = CharacterTimeService(store, key, seed.timezone, clock)
    assert service.snapshot().offline_duration is None
    t0 = clock.now_utc()
    service.record_successful_interaction()
    clock.advance(hours=8)
    snapshot = CharacterTimeService(
        SQLiteStore(tmp_path / "time.db"), key, seed.timezone, clock
    ).snapshot()
    assert snapshot.offline_duration == timedelta(hours=8)
    assert snapshot.now_local.hour == 16
    assert format_offline_duration(snapshot.offline_duration) == "约 8 小时"
    anchor = snapshot.last_interaction_at
    clock.set(t0 - timedelta(hours=1))
    rolled_back = service.snapshot()
    assert rolled_back.offline_duration == timedelta(0)
    assert rolled_back.diagnostics == ("clock_moved_backwards",)
    assert service.snapshot().last_interaction_at == anchor


def test_conversation_records_only_successful_completion_and_projects_time(
    tmp_path: Path,
) -> None:
    seed = _seed()
    key = seed.identity.internal_id
    clock = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    store = SQLiteStore(tmp_path / "conversation.db")

    class Client:
        def complete(self, messages: list[Message]) -> str:
            assert "当地日期：2026-09-30" in messages[0]["content"]
            assert "距离上次交流：无记录" in messages[0]["content"]
            return "好。"

    conversation = Conversation(
        Client(),
        CharacterProjector().project(seed),
        store,
        character_id=key,
        character_timezone=seed.timezone,
        clock=clock,
    )
    assert conversation.send("在吗") == "好。"
    assert (
        SQLiteStore(tmp_path / "conversation.db").get_last_interaction_at(key)
        == clock.now_utc()
    )

    class Failing:
        def complete(self, messages: list[Message]) -> str:
            raise RuntimeError("expected")

    failed = Conversation(
        Failing(),
        CharacterProjector().project(seed),
        store,
        character_id=key,
        character_timezone=seed.timezone,
        clock=FixedClock(datetime(2026, 10, 1, tzinfo=timezone.utc)),
    )
    with pytest.raises(RuntimeError):
        failed.send("失败")
    assert store.get_last_interaction_at(key) == clock.now_utc()


def test_time_prompt_does_not_expose_storage_metadata(tmp_path: Path) -> None:
    seed = _seed()
    clock = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    store = SQLiteStore(tmp_path / "prompt.db")
    store.record_last_interaction(
        seed.identity.internal_id, clock.now_utc() - timedelta(hours=2)
    )

    class Client:
        def complete(self, messages: list[Message]) -> str:
            content = messages[0]["content"]
            time_block = content.split("【当前时间】", 1)[1]
            assert "当地日期：2026-09-30" in time_block
            assert "当地时间：08:00" in time_block
            assert "约 2 小时" in time_block
            assert str(seed.identity.internal_id) not in time_block
            assert "timedelta" not in time_block and "UTC" not in time_block
            return "知道了。"

    conversation = Conversation(
        Client(),
        CharacterProjector().project(seed),
        store,
        character_id=seed.identity.internal_id,
        character_timezone=seed.timezone,
        clock=clock,
    )
    conversation.send("几点了？")


def test_state_elapsed_and_event_default_use_injected_clock(tmp_path: Path) -> None:
    from evolving_companion.character_events import CharacterEventService

    seed = _seed()
    clock = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    store = SQLiteStore(tmp_path / "state.db")
    state_service = CharacterStateService(store, clock)
    transition = CharacterStateTransitionService(state_service, clock)
    state = state_service.get_state(seed.identity.internal_id)
    state_service.save_state(state.model_copy(update={"energy": "low"}))
    clock.advance(hours=8)
    after = transition.apply_elapsed_time(seed.identity.internal_id).after_state
    assert after.energy == "medium"
    assert after.updated_at == clock.now_utc()
    events = CharacterEventService(
        seed.identity.internal_id, state_service, transition, clock
    )
    event = events.create_event(
        character_id=seed.identity.internal_id,
        event_type="rest_started",
        source="developer",
    )
    assert event.occurred_at == clock.now_utc()
