"""Offline action atomicity, durable idempotency and Life patch regressions."""

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from evolving_companion.backup import backup_database
from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import (
    CharacterLifeContext,
    CharacterLifeService,
    LifeContextData,
)
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.embeddings import LocalBGEEmbeddingProvider
from evolving_companion.llm import LLMClient
from evolving_companion.memory_providers import LocalBGERerankerProvider
from evolving_companion.npc import NPCService
from evolving_companion.observation import ObservationService
from evolving_companion.prompting import Message
from evolving_companion.restore import restore_database
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed
from evolving_companion.world_actions import ActionResolver, ActionResult, MoveToIntent

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 2, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Environment:
    store: SQLiteStore
    clock: FixedClock
    life: CharacterLifeService
    resolver: ActionResolver
    key: UUID
    home: UUID
    school: UUID


@pytest.fixture
def env(tmp_path: Path) -> Environment:
    store = SQLiteStore(tmp_path / "actions.db")
    clock = FixedClock(T0)
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    world = WorldEntityService(store, clock)
    world.initialize_seed_entities(load_world_seed(ROOT / "data/worlds/si_world.yaml"))
    home = world.resolve_place_name("住宅区的家")
    school = world.resolve_place_name("学校")
    life = CharacterLifeService(store, key, seed.initial_life_context, clock)
    life.update_life_context(key, current_location_entity_id=home)
    return Environment(
        store, clock, life, ActionResolver(store, key, clock), key, home, school
    )


def intent(env: Environment, destination: UUID, expected: UUID | None) -> MoveToIntent:
    return MoveToIntent(
        action_id=uuid4(),
        character_id=env.key,
        destination_entity_id=destination,
        expected_location_entity_id=expected,
    )


def current(env: Environment) -> CharacterLifeContext:
    value = env.store.get_character_life_context(env.key)
    assert value is not None
    return value


def receipt_count(env: Environment) -> int:
    with closing(sqlite3.connect(env.store.path)) as connection:
        return connection.execute(
            "SELECT count(*) FROM world_action_results"
        ).fetchone()[0]


def test_restore_before_action_removes_receipt_and_resolves_again(
    env: Environment, tmp_path: Path
) -> None:
    backup = backup_database(env.store.path, tmp_path / "backups")
    action = intent(env, env.school, env.home)
    first = env.resolver.resolve(action)
    assert first.status == "success" and not first.replayed
    assert (
        receipt_count(env) == 1
        and current(env).current_location_entity_id == env.school
    )
    restore_database(backup, env.store.path, tmp_path / "backups", service_stopped=True)
    assert (
        receipt_count(env) == 0 and current(env).current_location_entity_id == env.home
    )
    env.clock.advance(hours=1)
    second = env.resolver.resolve(action)
    assert second.status == "success" and not second.replayed
    assert current(env).current_location_entity_id == env.school
    assert current(env).updated_at == env.clock.now_utc()
    assert env.resolver.resolve(action).replayed


def test_runtime_life_writes_do_not_use_full_upsert(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Runtime Life must not use full snapshot upsert")

    monkeypatch.setattr(env.store, "upsert_character_life_context", forbidden)
    env.life.get_life_context(env.key)
    env.life.update_life_context(env.key, current_role="测试角色")
    assert env.resolver.resolve(intent(env, env.school, env.home)).status == "success"
    assert current(env).current_role == "测试角色"


def test_move_changes_only_location_and_timestamp_and_persists_receipt(
    env: Environment,
) -> None:
    before = current(env)
    action = intent(env, env.school, env.home)
    env.clock.advance(hours=1)
    result = env.resolver.resolve(action)
    assert result.status == "success" and result.reason == "moved"
    assert result.location_before == env.home and result.location_after == env.school
    assert result.resolved_at == env.clock.now_utc() and result.outcome_known
    assert not result.replayed
    after = current(env)
    assert (
        after.current_location_entity_id == env.school
        and after.updated_at == result.resolved_at
    )
    assert after.model_dump(
        exclude={"current_location_entity_id", "updated_at"}
    ) == before.model_dump(exclude={"current_location_entity_id", "updated_at"})
    assert after.character_id == env.key
    assert env.resolver.get_result(action.action_id) == result
    assert receipt_count(env) == 1
    with closing(sqlite3.connect(env.store.path)) as connection:
        row = connection.execute(
            "SELECT expected_location_entity_id, resolved_at FROM world_action_results"
        ).fetchone()
    assert row == (str(env.home), result.resolved_at.isoformat())


@pytest.mark.parametrize(
    "case",
    [
        "wrong_character",
        "missing_destination",
        "non_place",
        "missing_life",
        "stale_location",
    ],
)
def test_business_rejections_persist_without_location_change(
    env: Environment, case: str
) -> None:
    action = intent(env, env.school, env.home)
    reason = {
        "wrong_character": "character_id_mismatch",
        "missing_destination": "invalid_destination",
        "non_place": "invalid_destination",
        "missing_life": "life_context_missing",
        "stale_location": "location_precondition_failed",
    }[case]
    if case == "wrong_character":
        action = MoveToIntent.model_validate(
            action.model_dump() | {"character_id": UUID(int=999)}
        )
    elif case in {"missing_destination", "non_place"}:
        destination = UUID(int=999)
        if case == "non_place":
            with closing(sqlite3.connect(env.store.path)) as connection, connection:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute(
                    "INSERT INTO world_entities (entity_id, entity_type, canonical_name, created_at, updated_at) VALUES (?, 'object', '测试非地点', ?, ?)",
                    (str(destination), T0.isoformat(), T0.isoformat()),
                )
        action = MoveToIntent.model_validate(
            action.model_dump() | {"destination_entity_id": destination}
        )
    elif case == "missing_life":
        with closing(sqlite3.connect(env.store.path)) as connection, connection:
            connection.execute("DELETE FROM character_life_context")
    else:
        action = MoveToIntent.model_validate(
            action.model_dump() | {"expected_location_entity_id": env.school}
        )
    before = env.store.get_character_life_context(env.key)
    result = env.resolver.resolve(action)
    assert (
        result.status == "rejected" and result.reason == reason and result.outcome_known
    )
    assert env.store.get_character_life_context(env.key) == before
    assert env.resolver.get_result(action.action_id) == result
    assert receipt_count(env) == 1
    replay = env.resolver.resolve(action)
    assert replay.replayed and replay.reason == reason


@pytest.mark.parametrize("expected_unknown", [True, False])
def test_none_expected_is_an_explicit_precondition(
    env: Environment, expected_unknown: bool
) -> None:
    env.life.update_life_context(env.key, current_location_entity_id=None)
    before = current(env)
    action = intent(env, env.school, None if expected_unknown else env.home)
    result = env.resolver.resolve(action)
    assert result.location_before is None
    if expected_unknown:
        assert result.status == "success" and result.reason == "moved"
        assert current(env).current_location_entity_id == env.school
    else:
        assert (
            result.status == "rejected"
            and result.reason == "location_precondition_failed"
        )
        assert current(env) == before


@pytest.mark.parametrize("clock_backwards", [False, True])
def test_already_at_destination_is_noop_even_with_rollback(
    env: Environment, clock_backwards: bool
) -> None:
    before = current(env)
    env.clock.advance(hours=-1 if clock_backwards else 1)
    action = intent(env, env.home, env.home)
    result = env.resolver.resolve(action)
    assert result.status == "success" and result.reason == "already_at_destination"
    assert current(env) == before
    env.clock.advance(hours=2)
    replay = env.resolver.resolve(action)
    assert replay.replayed and replay.resolved_at == result.resolved_at
    assert current(env) == before and receipt_count(env) == 1


def test_clock_rollback_rejects_real_change(env: Environment) -> None:
    before = current(env)
    env.clock.advance(hours=-1)
    result = env.resolver.resolve(intent(env, env.school, env.home))
    assert result.status == "rejected" and result.reason == "clock_moved_backwards"
    assert current(env) == before


def test_a_b_a_replay_survives_restart_and_keeps_current_home(env: Environment) -> None:
    a = intent(env, env.school, env.home)
    first = env.resolver.resolve(a)
    env.clock.advance(hours=1)
    assert env.resolver.resolve(intent(env, env.home, env.school)).status == "success"
    before_replay = current(env)
    env.clock.advance(hours=1)
    restarted = ActionResolver(SQLiteStore(env.store.path), env.key, env.clock)
    replay = restarted.resolve(a)
    assert replay == ActionResult.model_validate(
        first.model_dump() | {"replayed": True}
    )
    assert replay.location_after == env.school  # Historical result, not current truth.
    assert (
        current(env) == before_replay
        and before_replay.current_location_entity_id == env.home
    )
    assert receipt_count(env) == 2


@pytest.mark.parametrize(
    "field", ["character_id", "destination_entity_id", "expected_location_entity_id"]
)
def test_action_id_conflict_never_overwrites_or_moves(
    env: Environment, field: str
) -> None:
    action = intent(env, env.school, env.home)
    original = env.resolver.resolve(action)
    before = current(env)
    conflicting = MoveToIntent.model_validate(
        action.model_dump() | {field: UUID(int=999)}
    )
    result = env.resolver.resolve(conflicting)
    assert result.status == "rejected" and result.reason == "action_id_conflict"
    assert not result.replayed
    assert env.resolver.get_result(action.action_id) == original
    assert current(env) == before and receipt_count(env) == 1


@pytest.mark.parametrize(
    "failure", ["receipt", "location", "receipt_ignore", "location_ignore"]
)
def test_write_failure_rolls_back_both_location_and_receipt(
    env: Environment, failure: str
) -> None:
    before = current(env)
    with closing(sqlite3.connect(env.store.path)) as connection, connection:
        if failure == "receipt":
            connection.execute(
                "CREATE TRIGGER fail_receipt BEFORE INSERT ON world_action_results BEGIN SELECT RAISE(ABORT, 'private test error'); END"
            )
        elif failure == "location":
            connection.execute(
                "CREATE TRIGGER fail_location BEFORE UPDATE OF current_location_entity_id ON character_life_context BEGIN SELECT RAISE(ABORT, 'private test error'); END"
            )
        elif failure == "receipt_ignore":
            connection.execute(
                "CREATE TRIGGER fail_receipt BEFORE INSERT ON world_action_results BEGIN SELECT RAISE(IGNORE); END"
            )
        else:
            connection.execute(
                "CREATE TRIGGER fail_location BEFORE UPDATE OF current_location_entity_id ON character_life_context BEGIN SELECT RAISE(IGNORE); END"
            )
    action = intent(env, env.school, env.home)
    result = env.resolver.resolve(action)
    assert (
        result.status == "failed"
        and result.reason == "storage_error"
        and result.outcome_known
    )
    assert "private test error" not in result.model_dump_json()
    assert current(env) == before
    assert env.resolver.get_result(action.action_id) is None and receipt_count(env) == 0


def test_two_connections_same_action_only_one_mutation(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = SQLiteStore(env.store.path)
    resolvers = (env.resolver, ActionResolver(other, env.key, env.clock))
    action = intent(env, env.school, env.home)
    gate = threading.Barrier(3)
    lock = threading.Lock()
    updates: list[str] = []

    def record(sql: str) -> None:
        if sql.startswith(
            "UPDATE character_life_context SET current_location_entity_id"
        ):
            with lock:
                updates.append(sql)

    for store in (env.store, other):
        original = store._connect

        def connect(original=original) -> sqlite3.Connection:
            connection = original()
            connection.set_trace_callback(record)
            return connection

        monkeypatch.setattr(store, "_connect", connect)

    def run(resolver: ActionResolver) -> ActionResult:
        gate.wait(timeout=5)
        return resolver.resolve(action)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(run, resolver) for resolver in resolvers]
        gate.wait(timeout=5)
        results = [future.result(timeout=10) for future in futures]
    assert all(result.status == "success" for result in results)
    assert sum(result.replayed for result in results) == 1
    assert len(updates) == 1 and receipt_count(env) == 1
    assert current(env).current_location_entity_id == env.school


def test_role_patch_does_not_write_stale_location_back(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = SQLiteStore(env.store.path)
    resolver = ActionResolver(other, env.key, env.clock)
    read = env.store.get_character_life_context
    interleaved = False

    def read_then_move(character_id: UUID) -> CharacterLifeContext | None:
        nonlocal interleaved
        stale = read(character_id)
        if not interleaved:
            interleaved = True
            assert (
                resolver.resolve(intent(env, env.school, env.home)).status == "success"
            )
        return stale

    # Deterministic old read -> another connection's committed move -> role patch.
    # This exact interleaving previously restored the stale home location.
    monkeypatch.setattr(env.store, "get_character_life_context", read_then_move)
    changed = env.life.update_life_context(env.key, current_role="测试新身份")
    assert interleaved and changed.current_role == "测试新身份"
    assert changed.current_location_entity_id == env.school
    assert read(env.key) == changed


def test_role_change_before_move_is_preserved(env: Environment) -> None:
    env.life.update_life_context(env.key, current_role="测试新身份")
    assert env.resolver.resolve(intent(env, env.school, env.home)).status == "success"
    assert current(env).current_role == "测试新身份"
    assert current(env).current_location_entity_id == env.school


def test_life_initializer_cannot_overwrite_concurrently_created_runtime(
    env: Environment,
) -> None:
    stale_seed = CharacterLifeContext.model_validate(
        current(env).model_dump()
        | {"current_role": "不应覆盖", "current_location_entity_id": None}
    )
    assert env.resolver.resolve(intent(env, env.school, env.home)).status == "success"
    before = current(env)
    assert env.store.initialize_character_life_context(stale_seed) == before
    assert current(env) == before


def test_patch_none_noop_utc_rollback_and_field_whitelist(env: Environment) -> None:
    env.clock.advance(hours=1)
    changed = env.life.update_life_context(env.key, current_role=None)
    assert changed.current_role is None and changed.updated_at == env.clock.now_utc()
    env.clock.advance(hours=-2)
    assert env.life.update_life_context(env.key, current_role=None) == changed
    with pytest.raises(ValueError, match="clock_moved_backwards"):
        env.life.update_life_context(env.key, current_role="不能向过去更新")
    local_now = (T0 + timedelta(hours=2)).astimezone(timezone(timedelta(hours=8)))
    patched = env.store.patch_character_life_context(
        env.key, LifeContextData(current_role="更新"), local_now
    )
    assert patched.updated_at == T0 + timedelta(hours=2)
    with pytest.raises(ValidationError):
        LifeContextData.model_validate({"character_id": str(UUID(int=9))})


def test_busy_database_never_reports_success(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = env.store._connect

    def nonblocking() -> sqlite3.Connection:
        connection = original()
        connection.execute(
            "PRAGMA busy_timeout = 0"
        )  # Test-only; no production tuning.
        return connection

    action = intent(env, env.school, env.home)
    before = current(env)
    monkeypatch.setattr(env.store, "_connect", nonblocking)
    with closing(sqlite3.connect(env.store.path)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        result = env.resolver.resolve(action)
        blocker.rollback()
    assert (
        result.status == "failed"
        and result.reason == "storage_error"
        and result.outcome_known
    )
    assert current(env) == before and receipt_count(env) == 0


@pytest.mark.parametrize("when", ["before_commit", "after_commit", "rollback_failure"])
def test_commit_failure_outcome_semantics(
    env: Environment, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    action = intent(env, env.school, env.home)
    before = current(env)

    class FaultyConnection(sqlite3.Connection):
        def commit(self) -> None:
            if when == "after_commit":
                super().commit()
            raise sqlite3.OperationalError("synthetic commit failure")

        def rollback(self) -> None:
            if when == "rollback_failure":
                raise sqlite3.OperationalError("synthetic rollback failure")
            super().rollback()

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(env.store.path, factory=FaultyConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(env.store, "_connect", connect)
        result = env.resolver.resolve(action)
    assert result.status == "failed"
    assert result.outcome_known == (when == "before_commit")
    assert result.reason == (
        "storage_error" if result.outcome_known else "commit_outcome_unknown"
    )
    if when == "after_commit":
        assert current(env).current_location_entity_id == env.school
        persisted = env.resolver.get_result(action.action_id)
        assert persisted is not None and persisted.status == "success"
        assert env.resolver.resolve(action).replayed
    else:
        assert current(env) == before and receipt_count(env) == 0


def test_missing_and_failed_result_lookup_are_distinct(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert env.resolver.get_result(uuid4()) is None

    def fail() -> sqlite3.Connection:
        raise sqlite3.OperationalError("private DB details")

    monkeypatch.setattr(env.store, "_connect", fail)
    with pytest.raises(RuntimeError, match="^action_result_lookup_failed$") as error:
        env.resolver.get_result(uuid4())
    assert "private DB details" not in str(error.value)
    result = env.resolver.resolve(intent(env, env.school, env.home))
    assert result.status == "failed" and result.outcome_known


def test_other_system_records_unchanged_and_observation_refreshes(
    env: Environment,
) -> None:
    CharacterStateService(env.store, env.clock).get_state(env.key)
    env.store.create_memory("semantic", "测试已有记忆", "explicit", "low")
    npc = NPCService(env.store, env.clock)
    home_person = npc.create("测试家中人物", place_entity_id=env.home)
    school_person = npc.create("测试学校人物", place_entity_id=env.school)

    def records() -> list[list[tuple[object, ...]]]:
        with closing(sqlite3.connect(env.store.path)) as connection:
            return [
                connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in (
                    "character_state",
                    "world_npcs",
                    "world_entities",
                    "memories",
                    "memory_evidence",
                    "memory_embeddings",
                    "memory_supersessions",
                    "archive_messages",
                    "character_runtime",
                )
            ]

    before = records()
    observation = ObservationService(env.store, env.clock)
    assert observation.observe(env.key).visible_npc_ids == (home_person.npc_id,)
    assert env.resolver.resolve(intent(env, env.school, env.home)).status == "success"
    assert observation.observe(env.key).visible_npc_ids == (school_person.npc_id,)
    assert records() == before


def test_no_models_no_background_and_chat_does_not_execute_actions(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Actions must not construct models or launch background work")

    for dependency in (LLMClient, LocalBGEEmbeddingProvider, LocalBGERerankerProvider):
        monkeypatch.setattr(dependency, "__init__", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(threading.Timer, "start", forbidden)
    monkeypatch.setattr(asyncio, "create_task", forbidden)
    monkeypatch.setattr(env.store, "create_memory", forbidden)
    assert env.resolver.resolve(intent(env, env.school, env.home)).status == "success"
    before = current(env)

    class FakeLLM:
        def complete(self, messages: list[Message]) -> str:
            return "我去家里吧。"

    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    chat = Conversation(
        FakeLLM(),
        CharacterProjector().project(seed),
        env.store,
        character_id=env.key,
        character_life_service=env.life,
        observation_service=ObservationService(env.store, env.clock),
        clock=env.clock,
    )
    assert chat.send("我去学校吧，move_to 家") == "我去家里吧。"
    assert current(env) == before and receipt_count(env) == 1
    tree = ast.parse(
        (ROOT / "src/evolving_companion/world_actions.py").read_text("utf-8")
    )
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports <= {
        "datetime",
        "typing",
        "uuid",
        "pydantic",
        "evolving_companion.clock",
    }
    assert not any(
        isinstance(node, (ast.AsyncFunctionDef, ast.Import)) for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"action_id": "invalid"},
        {"character_id": "SI-001"},
        {"action_type": "stay"},
        {"action_type": "generic_action"},
        {"destination_entity_id": "学校"},
        {"expected_location_entity_id": "家"},
        {"command": "move_to school"},
        {"sql": "DELETE FROM memories"},
        {"python": "print('test')"},
        {"shell": "echo test"},
    ],
)
def test_intent_rejects_untrusted_invalid_payloads(
    env: Environment, changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        MoveToIntent.model_validate(
            intent(env, env.school, env.home).model_dump() | changes
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "pending"},
        {"reason": "free form"},
        {"resolved_at": datetime(2026, 10, 2)},
        {"extra": "no"},
    ],
)
def test_result_strict_validation(env: Environment, changes: dict[str, object]) -> None:
    result = env.resolver.resolve(intent(env, env.school, env.home))
    with pytest.raises(ValidationError):
        ActionResult.model_validate(result.model_dump() | changes)


def test_intent_requires_explicit_expected_location(env: Environment) -> None:
    values = intent(env, env.school, env.home).model_dump()
    del values["expected_location_entity_id"]
    with pytest.raises(ValidationError):
        MoveToIntent.model_validate(values)


def test_sqlite_receipt_constraints_and_no_destination_fk(env: Environment) -> None:
    action = intent(env, UUID(int=999), env.home)
    assert env.resolver.resolve(action).reason == "invalid_destination"
    with closing(sqlite3.connect(env.store.path)) as connection, connection:
        assert (
            connection.execute(
                "PRAGMA foreign_key_list(world_action_results)"
            ).fetchall()
            == []
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE world_action_results SET status = 'failed'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE world_action_results SET action_type = 'stay'")
