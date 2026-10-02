"""Offline derived temporal context and its existing observation integration."""

import asyncio
from collections.abc import Mapping
from contextlib import closing
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.character_state_transition import (
    CharacterStateTransitionService,
)
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.embeddings import LocalBGEEmbeddingProvider
from evolving_companion.llm import LLMClient
from evolving_companion.memory_formation import MemoryFormationResult
from evolving_companion.memory_providers import LocalBGERerankerProvider
from evolving_companion.npc import NPCService
from evolving_companion.observation import ObservationService, ObservationSnapshot
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore
from evolving_companion.time_model import CharacterTimeService
from evolving_companion.world import WorldEntityService, load_world_seed
from evolving_companion.world_actions import ActionResolver, MoveToIntent
from evolving_companion.world_time import DayPeriod, WorldTimeService

ROOT = Path(__file__).resolve().parents[1]
ZONE = "Asia/Shanghai"


def local(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 2, hour, minute, tzinfo=ZoneInfo(ZONE))


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (5, 59, "night"),
        (6, 0, "morning"),
        (11, 59, "morning"),
        (12, 0, "afternoon"),
        (17, 59, "afternoon"),
        (18, 0, "evening"),
        (21, 59, "evening"),
        (22, 0, "night"),
    ],
)
def test_period_boundaries(hour: int, minute: int, expected: DayPeriod) -> None:
    snapshot = WorldTimeService(ZONE, FixedClock(local(hour, minute))).snapshot()
    assert snapshot.day_period == expected
    assert snapshot.now_local == local(hour, minute)
    assert snapshot.now_utc.utcoffset() == timedelta(0)
    assert snapshot.timezone_name == ZONE


@pytest.mark.parametrize("hours", [1, 8, 24, 24 * 7])
def test_midnight_offline_and_multiple_days(hours: int) -> None:
    clock = FixedClock(local(23))
    service = WorldTimeService(ZONE, clock)
    before = service.snapshot()
    clock.advance(hours=hours)
    after = service.snapshot()
    assert after.now_local == before.now_local + timedelta(hours=hours)
    assert after == service.snapshot() == WorldTimeService(ZONE, clock).snapshot()
    assert after.day_period == ("morning" if hours == 8 else "night")


def test_utc_conversion_and_explicit_time_does_not_read_clock() -> None:
    class UnusedClock:
        def now_utc(self) -> datetime:
            raise AssertionError("explicit snapshot must not read Clock")

    service = WorldTimeService(ZONE, UnusedClock())
    stamp = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)
    snapshot = service.snapshot(stamp)
    assert snapshot.now_local == local(6)
    assert snapshot.day_period == "morning"
    assert snapshot == service.snapshot(local(6))
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "day_period", "night")
    with pytest.raises(ValueError, match="timezone-aware"):
        service.snapshot(datetime(2026, 10, 2))


def test_explicit_timezone_not_device_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    stamp = datetime(2026, 10, 2, 0, tzinfo=timezone.utc)
    assert WorldTimeService(ZONE).snapshot(stamp).day_period == "morning"
    assert WorldTimeService("UTC").snapshot(stamp).day_period == "night"


def test_clock_rollback_recomputes_without_an_anchor() -> None:
    clock = FixedClock(local(10))
    service = WorldTimeService(ZONE, clock)
    original = service.snapshot()
    assert original.day_period == "morning"
    clock.set(local(5))
    assert service.snapshot().day_period == "night"
    clock.set(local(10))
    assert service.snapshot() == original


def test_known_place_prompt_has_no_duplicate_time_section() -> None:
    snapshot = ObservationSnapshot(
        character_id=uuid4(),
        observed_at=local(22),
        location_entity_id=uuid4(),
        place_name="测试地点",
        status="available",
        day_period="night",
    )
    block = PromptBuilder._build_observation_context(snapshot)
    assert "当前时段：夜间。" in block
    assert '当前地点："测试地点"。' in block
    assert "【当前时间】" not in block and "【世界时间】" not in block
    for metadata in (
        "2026",
        "22:00",
        "UTC",
        ZONE,
        "day_period",
        str(snapshot.character_id),
    ):
        assert metadata not in block
    assert "不代表睡眠、上课、营业、移动或离线经历" in block


def dump(path: Path) -> tuple[str, ...]:
    with closing(sqlite3.connect(path)) as connection:
        return tuple(connection.iterdump())


@pytest.mark.parametrize("elapsed", [8, 24 * 5, -18])
def test_temporal_reads_leave_entire_database_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elapsed: int
) -> None:
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(local(23))
    store = SQLiteStore(tmp_path / "time.db")
    world = WorldEntityService(store, clock)
    world.initialize_seed_entities(load_world_seed(ROOT / "data/worlds/si_world.yaml"))
    home = world.resolve_place_name("住宅区的家")
    school = world.resolve_place_name("学校")
    life = CharacterLifeService(store, key, seed.initial_life_context, clock)
    life.update_life_context(key, current_location_entity_id=home)
    registry = NPCService(store, clock)
    npc = registry.create("Synthetic NPC", place_entity_id=home)
    state = CharacterStateService(store, clock).get_state(key)
    store.create_memory("semantic", "Synthetic memory", "explicit", "medium")
    store.record_last_interaction(key, clock.now_utc())
    action = MoveToIntent(
        action_id=uuid4(),
        character_id=key,
        destination_entity_id=school,
        expected_location_entity_id=home,
    )
    receipt = ActionResolver(store, key, clock).resolve(action)
    assert receipt.status == "success"
    before_life = life.get_life_context(key)
    before = dump(store.path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("temporal context must not start work or call models")

    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(threading.Timer, "start", forbidden)
    monkeypatch.setattr(asyncio, "create_task", forbidden)
    monkeypatch.setattr(LLMClient, "complete", forbidden)
    monkeypatch.setattr(LocalBGEEmbeddingProvider, "encode", forbidden)
    monkeypatch.setattr(LocalBGERerankerProvider, "score", forbidden)
    service = WorldTimeService(seed.timezone, clock)
    observation = ObservationService(store, clock, world_time_service=service)
    assert service.snapshot().day_period == "night"
    clock.advance(hours=elapsed)
    snapshot = service.snapshot()
    observed = observation.observe(key)
    assert observed.observed_at == snapshot.now_utc
    assert observed.day_period == snapshot.day_period
    assert snapshot.day_period == ("morning" if elapsed == 8 else "night")
    assert life.get_life_context(key) == before_life
    assert registry.get(npc.npc_id) == npc
    assert store.get_character_state(key) == state
    assert store.get_world_action_result(action.action_id) == receipt
    assert dump(store.path) == before  # Tables, rows, timestamps, Memory and receipts.
    reopened = SQLiteStore(store.path)
    restarted = WorldTimeService(seed.timezone, clock)
    assert restarted.snapshot() == snapshot
    assert (
        ObservationService(reopened, clock, world_time_service=restarted).observe(key)
        == observed
    )
    assert dump(store.path) == before


@pytest.mark.parametrize("life_exists", [False, True])
def test_unknown_location_keeps_temporal_context_without_guessing(
    tmp_path: Path, life_exists: bool
) -> None:
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(local(22))
    store = SQLiteStore(tmp_path / "unknown.db")
    if life_exists:
        WorldEntityService(store, clock).initialize_seed_entities(
            load_world_seed(ROOT / "data/worlds/si_world.yaml")
        )
        CharacterLifeService(
            store, key, seed.initial_life_context, clock
        ).get_life_context(key)
    before = dump(store.path)
    observation = ObservationService(
        store, clock, world_time_service=WorldTimeService(seed.timezone, clock)
    ).observe(key)
    assert observation.status == "unknown_location"
    assert observation.location_entity_id is None and observation.place_name is None
    assert observation.visible_npc_ids == () and observation.day_period == "night"
    block = PromptBuilder._build_observation_context(observation)
    assert "当前时段：夜间。" in block and "当前位置未知" in block
    assert "当前地点：" not in block and "周围无人" not in block
    assert "不代表睡眠" in block and "离线经历" in block
    assert "UTC" not in block and ZONE not in block and str(key) not in block
    assert dump(store.path) == before


def test_conversation_reads_clock_once_and_shares_now_across_all_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id

    class CountingClock(FixedClock):
        calls = 0

        def now_utc(self) -> datetime:
            self.calls += 1
            result = super().now_utc()
            self.advance(minutes=1)  # Would cross 22:00 on a second read.
            return result

    clock = CountingClock(local(21, 59))
    stamp = local(21, 59).astimezone(timezone.utc)
    store = SQLiteStore(tmp_path / "conversation.db")
    state_service = CharacterStateService(store, clock)
    # Explicit initialization does not consume the turn clock.
    state_service.get_state(key, initialize_at=stamp)
    world_time = WorldTimeService(seed.timezone, clock)
    observation = ObservationService(store, clock, world_time_service=world_time)
    seen: dict[str, datetime | None] = {}
    original_world = world_time.snapshot
    original_time = CharacterTimeService.snapshot
    original_state = CharacterStateTransitionService.apply_elapsed_time
    original_observation = store.capture_observation_context

    def world_snapshot(now_utc: datetime | None = None):
        seen["world"] = now_utc
        return original_world(now_utc)

    def time_snapshot(service: CharacterTimeService, now_utc: datetime | None = None):
        seen["time"] = now_utc
        return original_time(service, now_utc)

    def state_transition(
        service: CharacterStateTransitionService,
        character_id: UUID,
        now: datetime | None = None,
    ):
        seen["state"] = now
        return original_state(service, character_id, now)

    def capture(character_id: UUID, observed_at: datetime):
        seen["observation"] = observed_at
        return original_observation(character_id, observed_at)

    monkeypatch.setattr(world_time, "snapshot", world_snapshot)
    monkeypatch.setattr(CharacterTimeService, "snapshot", time_snapshot)
    monkeypatch.setattr(
        CharacterStateTransitionService, "apply_elapsed_time", state_transition
    )
    monkeypatch.setattr(store, "capture_observation_context", capture)

    class Client:
        calls = 0

        def complete(self, messages: list[Message]) -> str:
            self.calls += 1
            assert "当地时间：21:59" in messages[0]["content"]
            assert "当前时段：傍晚。" in messages[0]["content"]
            assert "【世界时间】" not in messages[0]["content"]
            assert messages[0]["content"].count("【当前可观察环境】") == 1
            return "好。"

    client = Client()
    conversation = Conversation(
        client,
        CharacterProjector().project(seed),
        store,
        character_id=key,
        character_timezone=seed.timezone,
        clock=clock,
        character_state_service=state_service,
        observation_service=observation,
    )
    assert conversation.send("在吗") == "好。"
    assert clock.calls == 1 and client.calls == 1
    assert seen == dict.fromkeys(("world", "time", "state", "observation"), stamp)
    assert store.get_last_interaction_at(key) == stamp
    assert conversation.history == (
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "好。"},
    )
    assert store.list_active_memories() == ()


def test_temporal_failure_drops_combined_context_but_completes_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(local(21, 59))
    store = SQLiteStore(tmp_path / "failure.db")
    world = WorldEntityService(store, clock)
    world.initialize_seed_entities(load_world_seed(ROOT / "data/worlds/si_world.yaml"))
    home = world.resolve_place_name("住宅区的家")
    life = CharacterLifeService(store, key, seed.initial_life_context, clock)
    life.update_life_context(key, current_location_entity_id=home)
    temporal = WorldTimeService(seed.timezone, clock)
    observation = ObservationService(store, clock, world_time_service=temporal)

    class Client:
        calls: list[list[Message]]

        def __init__(self) -> None:
            self.calls = []

        def complete(self, messages: list[Message]) -> str:
            self.calls.append(messages)
            return "好。"

    class Formation:
        calls = 0

        def process_turn(
            self,
            user_archive_message: Mapping[str, str],
            assistant_archive_message: Mapping[str, str],
        ) -> MemoryFormationResult:
            self.calls += 1
            assert user_archive_message["role"] == "user"
            assert assistant_archive_message["role"] == "assistant"
            return MemoryFormationResult()

    client, formation = Client(), Formation()
    chat = Conversation(
        client,
        CharacterProjector().project(seed),
        store,
        character_id=key,
        character_timezone=seed.timezone,
        clock=clock,
        observation_service=observation,
        memory_formation_service=formation,
    )
    chat.send("你好")
    assert "当前时段：傍晚。" in client.calls[0][0]["content"]
    before_life = store.get_character_life_context(key)

    def protected_rows() -> tuple[list[tuple], ...]:
        with closing(sqlite3.connect(store.path)) as connection:
            return tuple(
                connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in (
                    "character_state",
                    "memories",
                    "memory_evidence",
                    "world_action_results",
                )
            )

    before = protected_rows()
    clock.advance(hours=1)

    def fail(now_utc: datetime | None = None):
        raise ValueError("private temporal detail")

    monkeypatch.setattr(temporal, "snapshot", fail)
    assert chat.send("继续") == "好。"
    assert chat.last_observation_error == "ValueError"
    prompt = client.calls[-1][0]["content"]
    for text in (
        "【当前生活上下文】",
        "【当前可观察环境】",
        "当前时段：",
        "private temporal detail",
        "ValueError",
    ):
        assert text not in prompt
    assert "private temporal detail" not in caplog.text
    assert "observation_failed: ValueError" in caplog.text
    assert protected_rows() == before
    assert store.get_character_life_context(key) == before_life
    assert len(chat.history) == 4 and formation.calls == len(client.calls) == 2
    assert store.get_last_interaction_at(key) == clock.now_utc()
    with closing(sqlite3.connect(store.path)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM archive_messages").fetchone()[0]
            == 4
        )


def test_eight_hour_offline_context_does_not_assert_lived_experience(
    tmp_path: Path,
) -> None:
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(local(22))
    store = SQLiteStore(tmp_path / "offline.db")
    time = CharacterTimeService(store, key, seed.timezone, clock)
    time.record_successful_interaction()
    clock.advance(hours=8)
    now = clock.now_utc()
    observation = ObservationService(
        store, clock, world_time_service=WorldTimeService(seed.timezone, clock)
    ).observe(key, now)
    prompt = PromptBuilder(CharacterProjector().project(seed)).build(
        [], "在吗", character_time=time.snapshot(now), observation=observation
    )[0]["content"]
    assert "约 8 小时" in prompt and "当前时段：早晨。" in prompt
    for fabricated in ("玲睡了八小时", "玲刚起床", "玲度过了一夜", "玲早上去了学校"):
        assert fabricated not in prompt
    assert "不代表这段时间发生过任何具体经历或活动" in prompt
    assert store.list_active_memories() == ()
    assert store.get_character_life_context(key) is None
    assert store.get_character_state(key) is None


def test_no_temporal_service_preserves_existing_unknown_observation_prompt() -> None:
    assert (
        PromptBuilder._build_observation_context(
            ObservationSnapshot(character_id=uuid4(), observed_at=local(22))
        )
        == ""
    )
