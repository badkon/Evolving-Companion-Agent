"""Offline NPC persistence and World/Character boundary checks."""

import ast
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.clock import FixedClock
from evolving_companion.embeddings import LocalBGEEmbeddingProvider
from evolving_companion.llm import LLMClient
from evolving_companion.memory_providers import LocalBGERerankerProvider
from evolving_companion.npc import NPCRecord, NPCService
from evolving_companion.prompting import PromptBuilder
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 2, tzinfo=timezone.utc)


def setup_registry(
    path: Path,
) -> tuple[SQLiteStore, FixedClock, WorldEntityService, NPCService]:
    store = SQLiteStore(path)
    clock = FixedClock(T0)
    world = WorldEntityService(store, clock)
    world.initialize_seed_entities(load_world_seed(ROOT / "data/worlds/si_world.yaml"))
    return store, clock, world, NPCService(store, clock)


def test_create_get_identity_active_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "npc.db"
    store, clock, _, service = setup_registry(path)
    assert service.list_active() == ()
    assert service.get(UUID(int=999)) is None
    first = service.create("测试 NPC", display_name="测试显示名", tags=("student",))
    second = service.create("测试 NPC", active=False)
    assert isinstance(first.npc_id, UUID)
    assert first.npc_id != second.npc_id
    assert first.created_at == first.updated_at == T0
    assert first.place_entity_id is None
    assert service.get(first.npc_id) == first
    assert service.list_active() == (first,)
    clock.advance(hours=1)
    changed = service.update(first.npc_id, canonical_name="新测试名称")
    assert changed.npc_id == first.npc_id
    inactive = service.set_active(first.npc_id, False)
    assert service.list_active() == ()
    reopened = NPCService(SQLiteStore(path), clock)
    assert reopened.get(first.npc_id) == inactive
    assert reopened.get(second.npc_id) == second
    assert reopened.set_active(first.npc_id, True).npc_id == first.npc_id
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT count(*) FROM world_npcs").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_npc(first)


@pytest.mark.parametrize("other_field", ["location", "active", "tags"])
def test_patch_preserves_interleaved_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, other_field: str
) -> None:
    path = tmp_path / "interleaved.db"
    store, clock, world, service = setup_registry(path)
    home = world.resolve_place_name("住宅区的家")
    school = world.resolve_place_name("学校")
    npc = service.create("测试", display_name="旧名", place_entity_id=home)
    other = NPCService(SQLiteStore(path), clock)
    original_patch = store.patch_npc
    clock.advance(hours=1)

    def interleave(
        npc_id: UUID, changes: dict[str, object], now: datetime
    ) -> NPCRecord:
        # Deterministic second writer before this writer acquires its transaction.
        if other_field == "location":
            other.set_location(npc_id, school)
        elif other_field == "active":
            other.set_active(npc_id, False)
        else:
            other.update(npc_id, tags=("new",))
        return original_patch(npc_id, changes, now)

    monkeypatch.setattr(store, "patch_npc", interleave)
    changed = service.update(npc.npc_id, display_name="新名")
    assert changed == other.get(npc.npc_id)
    assert changed.display_name == "新名"
    assert changed.place_entity_id == (school if other_field == "location" else home)
    assert changed.active == (other_field != "active")
    assert changed.tags == (("new",) if other_field == "tags" else ())
    assert changed.npc_id == npc.npc_id and changed.created_at == npc.created_at
    assert changed.updated_at == clock.now_utc()


def test_invalid_patch_rolls_back_and_identity_is_not_patchable(tmp_path: Path) -> None:
    store, clock, _, service = setup_registry(tmp_path / "rollback.db")
    npc = service.create("测试")
    clock.advance(hours=1)
    with pytest.raises(ValidationError):
        store.patch_npc(npc.npc_id, {"canonical_name": ""}, clock.now_utc())
    with pytest.raises(ValueError, match="Unsupported"):
        store.patch_npc(npc.npc_id, {"npc_id": UUID(int=1)}, clock.now_utc())
    assert service.get(npc.npc_id) == npc


def test_explicit_location_and_partial_updates(tmp_path: Path) -> None:
    store, clock, world, service = setup_registry(tmp_path / "npc.db")
    school = world.resolve_place_name("学校")
    area = world.resolve_place_name("住宅区")
    npc = service.create("测试学生", place_entity_id=school)
    places = world.list_entities()
    clock.advance(hours=1)
    assert service.get(npc.npc_id) == npc  # Elapsed time does not move NPCs.
    assert service.update(npc.npc_id) == npc
    assert service.set_location(npc.npc_id, school) == npc
    assert service.set_active(npc.npc_id, True) == npc
    updated = service.set_location(npc.npc_id, area)
    assert updated.place_entity_id == area
    assert updated.updated_at == clock.now_utc()
    assert updated.created_at == T0
    assert updated.npc_id == npc.npc_id
    clock.advance(hours=1)
    cleared = service.update(
        npc.npc_id,
        display_name="测试别名",
        short_description="仅测试的描述",
        tags=("test",),
        place_entity_id=None,
    )
    assert cleared.place_entity_id is None
    assert cleared.tags == ("test",)
    cleared_text = service.update(npc.npc_id, display_name=None, short_description=None)
    assert cleared_text.display_name is None
    assert cleared_text.short_description is None
    assert cleared_text.tags == ("test",)
    assert world.list_entities() == places
    assert store.list_active_memories() == ()
    with pytest.raises(KeyError):
        service.update(UUID(int=999), active=False)


@pytest.mark.parametrize("entity_kind", ["missing", "non_place"])
def test_invalid_location_rejected_on_create_update_and_direct_sql(
    tmp_path: Path, entity_kind: str
) -> None:
    store, _, world, service = setup_registry(tmp_path / "npc.db")
    school = world.resolve_place_name("学校")
    invalid_id = UUID(int=999)
    if entity_kind == "non_place":
        # B4 currently admits only places. Simulate a future non-place entity
        # in this isolated DB to exercise B5's independent type guard.
        with closing(sqlite3.connect(store.path)) as connection, connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute(
                """INSERT INTO world_entities
                   (entity_id, entity_type, canonical_name, created_at, updated_at)
                   VALUES (?, 'object', '测试非地点', ?, ?)""",
                (str(invalid_id), T0.isoformat(), T0.isoformat()),
            )
    npc = service.create("测试 NPC", place_entity_id=school)
    with pytest.raises(sqlite3.IntegrityError, match="existing Place"):
        service.create("非法位置 NPC", place_entity_id=invalid_id)
    with pytest.raises(sqlite3.IntegrityError, match="existing Place"):
        service.set_location(npc.npc_id, invalid_id)
    assert service.get(npc.npc_id) == npc
    assert service.list_active() == (npc,)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="existing Place"):
            connection.execute(
                "UPDATE world_npcs SET place_entity_id = ? WHERE npc_id = ?",
                (str(invalid_id), str(npc.npc_id)),
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"npc_id": "not-a-uuid"},
        {"canonical_name": " "},
        {"display_name": " "},
        {"tags": [""]},
        {"place_entity_id": "not-a-uuid"},
        {"created_at": datetime(2026, 1, 1)},
        {"updated_at": datetime(2026, 1, 1)},
        {"memory": "not a Character"},
    ],
)
def test_record_validation(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NPCRecord.model_validate(
            {
                "npc_id": UUID(int=1),
                "canonical_name": "测试",
                "created_at": T0,
                "updated_at": T0,
            }
            | changes
        )


def test_utc_and_clock_rollback_preserve_record(tmp_path: Path) -> None:
    store, clock, _, service = setup_registry(tmp_path / "npc.db")
    npc = service.create("测试")
    local = timezone(timedelta(hours=8))
    record = NPCRecord.model_validate(
        {**npc.model_dump(), "created_at": T0.astimezone(local)}
    )
    assert record.created_at.tzinfo == timezone.utc
    clock.advance(hours=-1)
    assert service.update(npc.npc_id) == npc
    with pytest.raises(ValueError, match="clock_moved_backwards"):
        service.update(npc.npc_id, canonical_name="不会保存")
    assert store.get_npc(npc.npc_id) == npc


def test_character_prompt_state_life_memory_and_places_unchanged(
    tmp_path: Path,
) -> None:
    store, clock, world, service = setup_registry(tmp_path / "npc.db")
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    life = CharacterLifeService(store, key, seed.initial_life_context, clock)
    life_before = life.get_life_context(key)
    state = CharacterStateService(store, clock).get_state(key)
    memory = store.create_memory("semantic", "测试已有记忆", "explicit", "medium")
    places = world.list_entities()
    builder = PromptBuilder(CharacterProjector().project(seed))
    before = builder.build(
        [], "谁在学校？", character_life_context=life.project_context(key, world)
    )
    npc = service.create(
        "绝不自动投影的测试 NPC",
        place_entity_id=world.resolve_place_name("学校"),
        short_description="绝不自动投影的测试描述",
    )
    service.set_location(npc.npc_id, world.resolve_place_name("住宅区"))
    service.set_active(npc.npc_id, False)
    after = builder.build(
        [], "谁在学校？", character_life_context=life.project_context(key, world)
    )
    assert before == after  # NPC location adds no prompt content either.
    assert npc.canonical_name not in after[0]["content"]
    assert "绝不自动投影的测试描述" not in after[0]["content"]
    assert str(npc.npc_id) not in after[0]["content"]
    assert store.get_character_state(key) == state
    assert life.get_life_context(key) == life_before
    assert store.list_active_memories() == (memory,)
    assert world.list_entities() == places
    with closing(sqlite3.connect(store.path)) as connection:
        for table in ("memory_evidence", "memory_embeddings", "archive_messages"):
            assert (
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
            )


def test_registry_has_no_model_calls_or_background_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("NPC operation must not construct models or start threads")

    monkeypatch.setattr(LLMClient, "__init__", forbidden)
    monkeypatch.setattr(LocalBGEEmbeddingProvider, "__init__", forbidden)
    monkeypatch.setattr(LocalBGERerankerProvider, "__init__", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    _, clock, world, service = setup_registry(tmp_path / "npc.db")
    npc = service.create("测试", place_entity_id=world.resolve_place_name("学校"))
    service.set_location(npc.npc_id, None)
    service.set_active(npc.npc_id, False)
    clock.advance(days=365)
    assert service.list_active() == ()
    inactive = service.get(npc.npc_id)
    assert inactive is not None and inactive.updated_at == T0
    tree = ast.parse((ROOT / "src/evolving_companion/npc.py").read_text("utf-8"))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert imports <= {
        "datetime",
        "enum",
        "typing",
        "uuid",
        "pydantic",
        "evolving_companion.clock",
    }
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in ast.walk(tree))
