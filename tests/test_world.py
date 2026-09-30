"""Offline identity, legacy migration and World/Life boundary checks."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.cli import _handle_life_command
from evolving_companion.clock import FixedClock
from evolving_companion.prompting import PromptBuilder
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import (
    WorldEntity,
    WorldEntityService,
    WorldSeedData,
    load_world_seed,
)

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 9, 30, tzinfo=timezone.utc)


def setup_world(path: Path) -> tuple[SQLiteStore, FixedClock, WorldEntityService]:
    store = SQLiteStore(path)
    clock = FixedClock(T0)
    world = WorldEntityService(store, clock)
    world.initialize_seed_entities(load_world_seed(ROOT / "data/worlds/si_world.yaml"))
    return store, clock, world


def test_seed_stability_parent_restart_and_runtime_authority(tmp_path: Path) -> None:
    path = tmp_path / "world.db"
    store, clock, world = setup_world(path)
    original = world.list_entities()
    assert len(original) == 4
    home = world.get_place(world.resolve_place_name("住宅区的家"))
    assert home.parent_entity_id == world.resolve_place_name("住宅区")
    assert home.created_at == home.updated_at == T0
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE world_entities SET canonical_name = ? WHERE entity_id = ?",
            ("家", str(home.entity_id)),
        )
    clock.advance(hours=12)
    world.initialize_seed_entities(load_world_seed(ROOT / "data/worlds/si_world.yaml"))
    assert len(world.list_entities()) == 4
    assert world.resolve_display_name(home.entity_id) == "家"
    assert (
        WorldEntityService(SQLiteStore(path)).list_entities() == world.list_entities()
    )
    assert store.list_active_memories() == ()


def test_seed_can_add_missing_entities_and_insert_child_first(tmp_path: Path) -> None:
    store, _, world = setup_world(tmp_path / "world.db")
    seed = load_world_seed(ROOT / "data/worlds/si_world.yaml")
    # Reverse order deliberately places the child before its parent.
    reversed_seed = WorldSeedData(entities=tuple(reversed(seed.entities)))
    fresh = WorldEntityService(SQLiteStore(tmp_path / "child-first.db"), FixedClock(T0))
    fresh.initialize_seed_entities(reversed_seed)
    assert len(fresh.list_entities()) == 4
    world.initialize_seed_entities(reversed_seed)
    assert len(store.list_world_entities()) == 4
    extra = WorldSeedData.model_validate(
        {"entities": [{"entity_id": str(UUID(int=30)), "canonical_name": "测试地点"}]}
    )
    world.initialize_seed_entities(extra)
    assert len(world.list_entities()) == 5


@pytest.mark.parametrize("problem", ["duplicate", "missing_parent", "cycle"])
def test_seed_rejects_invalid_identity_and_containment(problem: str) -> None:
    first = {"entity_id": str(UUID(int=1)), "canonical_name": "测试甲"}
    second = {"entity_id": str(UUID(int=2)), "canonical_name": "测试乙"}
    if problem == "duplicate":
        second["entity_id"] = first["entity_id"]
    elif problem == "missing_parent":
        first["parent_entity_id"] = str(UUID(int=3))
    else:
        first["parent_entity_id"] = second["entity_id"]
        second["parent_entity_id"] = first["entity_id"]
    with pytest.raises(ValidationError):
        WorldSeedData.model_validate({"entities": [first, second]})


def test_world_and_sqlite_reject_non_place_or_orphan_parent(tmp_path: Path) -> None:
    store, _, _ = setup_world(tmp_path / "world.db")
    with pytest.raises(ValidationError):
        WorldEntity.model_validate(
            {
                "entity_id": UUID(int=50),
                "entity_type": "npc",
                "canonical_name": "测试",
                "created_at": T0,
                "updated_at": T0,
            }
        )
    orphan = WorldEntity(
        entity_id=UUID(int=51),
        canonical_name="测试",
        parent_entity_id=UUID(int=52),
        created_at=T0,
        updated_at=T0,
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_world_seed((orphan,))
    assert store.get_world_entity(orphan.entity_id) is None


@pytest.mark.parametrize("old_location", ["学校", "学校附近", "不存在的地点"])
def test_legacy_b3_migration_exact_only_once(tmp_path: Path, old_location: str) -> None:
    path = tmp_path / "legacy.db"
    key = load_character_seed_data(
        ROOT / "data/characters/si_001.yaml"
    ).identity.internal_id
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("""CREATE TABLE character_life_context (
            character_id TEXT PRIMARY KEY, life_stage TEXT, home_reference TEXT,
            school_reference TEXT, primary_area_reference TEXT,
            current_location_reference TEXT, current_role TEXT, updated_at TEXT)""")
        connection.execute(
            "INSERT INTO character_life_context VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(key),
                "student",
                "住宅区的家",
                "学校",
                "教育生活区",
                old_location,
                "学生",
                T0.isoformat(),
            ),
        )
    store = SQLiteStore(path)
    with pytest.raises(ValueError, match="Initialize World"):
        store.get_character_life_context(key)
    world = WorldEntityService(store, FixedClock(T0))
    seed = load_world_seed(ROOT / "data/worlds/si_world.yaml")
    diagnostics = world.initialize_seed_entities(seed)
    context = store.get_character_life_context(key)
    assert context is not None
    assert context.home_entity_id == world.resolve_place_name("住宅区的家")
    assert context.school_entity_id == world.resolve_place_name("学校")
    assert context.primary_area_entity_id == world.resolve_place_name("教育生活区")
    assert context.updated_at == T0
    if old_location == "学校":
        assert context.current_location_entity_id == world.resolve_place_name("学校")
        assert diagnostics == ()
    else:
        assert context.current_location_entity_id is None
        assert len(diagnostics) == 1
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT current_location_reference FROM character_life_context"
            ).fetchone()[0]
            == old_location
        )
    life = CharacterLifeService(store, key, clock=FixedClock(T0))
    cleared = life.update_life_context(key, home_entity_id=None)
    assert world.initialize_seed_entities(seed) == ()
    assert SQLiteStore(path).get_character_life_context(key) == cleared


def test_ambiguous_names_rejected_by_resolution_and_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, clock, world = setup_world(tmp_path / "world.db")
    extra = WorldSeedData.model_validate(
        {"entities": [{"entity_id": str(UUID(int=60)), "canonical_name": "学校"}]}
    )
    world.initialize_seed_entities(extra)
    with pytest.raises(ValueError, match="ambiguous"):
        world.resolve_place_name("学校")
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    life = CharacterLifeService(
        store, seed.identity.internal_id, seed.initial_life_context, clock
    )
    before = life.get_life_context(seed.identity.internal_id)
    _handle_life_command("/life location 学校", life, seed.identity.internal_id, world)
    assert "ambiguous" in capsys.readouterr().out
    assert life.get_life_context(seed.identity.internal_id) == before


def test_life_validation_prompt_and_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "world.db"
    store, clock, world = setup_world(path)
    seed = load_character_seed_data(ROOT / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    life = CharacterLifeService(store, key, seed.initial_life_context, clock)
    state = CharacterStateService(store, clock).update_state(
        key, current_activity="看漫画"
    )
    before = life.get_life_context(key)
    with pytest.raises(ValueError, match="existing Place"):
        life.update_life_context(key, current_location_entity_id=UUID(int=999))
    assert life.get_life_context(key) == before
    school = world.resolve_place_name("学校")
    invalid_context = type(before).model_validate(
        {**before.model_dump(), "current_location_entity_id": UUID(int=999)}
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_character_life_context(invalid_context)
    assert life.get_life_context(key) == before
    changed = life.update_life_context(key, current_location_entity_id=school)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE world_entities SET description = ? WHERE entity_id = ?",
            ("不应自动成为角色知识的秘密描述", str(school)),
        )
    prompt = PromptBuilder(CharacterProjector().project(seed)).build(
        [], "你在哪？", character_life_context=life.project_context(key, world)
    )[0]["content"]
    block = prompt.split("【当前生活上下文】")[1]
    assert "当前地点：学校" in block and "家：住宅区的家" in block
    for entity in world.list_entities():
        assert str(entity.entity_id) not in prompt
    for name in (
        "entity_id",
        "parent_entity_id",
        "created_at",
        "updated_at",
        "不应自动成为角色知识的秘密描述",
    ):
        assert name not in prompt
    clock.advance(hours=12)
    assert life.get_life_context(key) == changed
    reopened = CharacterLifeService(
        SQLiteStore(path), key, seed.initial_life_context, clock
    )
    assert reopened.get_life_context(key) == changed
    assert store.get_character_state(key) == state
    assert store.list_active_memories() == ()


def test_ambiguous_seed_name_migration_does_not_choose_one(tmp_path: Path) -> None:
    store, _, world = setup_world(tmp_path / "world.db")
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            """INSERT INTO character_life_context
            (character_id, life_stage, school_reference, updated_at)
            VALUES (?, 'student', '学校', ?)""",
            (str(UUID(int=100)), T0.isoformat()),
        )
    seed = load_world_seed(ROOT / "data/worlds/si_world.yaml")
    extra = WorldSeedData.model_validate(
        {"entities": [{"entity_id": str(UUID(int=101)), "canonical_name": "学校"}]}
    )
    diagnostics = world.initialize_seed_entities(
        WorldSeedData(entities=seed.entities + extra.entities)
    )
    assert len(diagnostics) == 1
    context = store.get_character_life_context(UUID(int=100))
    assert context is not None and context.school_entity_id is None
