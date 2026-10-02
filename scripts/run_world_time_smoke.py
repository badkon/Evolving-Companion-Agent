"""Offline B8 and combined B6/B7/B8 checks, using only temporary SQLite."""

from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.clock import FixedClock
from evolving_companion.npc import NPCService
from evolving_companion.observation import ObservationService
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed
from evolving_companion.world_actions import ActionResolver, MoveToIntent
from evolving_companion.world_time import WorldTimeService


def dump(path: Path) -> tuple[str, ...]:
    with closing(sqlite3.connect(path)) as connection:
        return tuple(connection.iterdump())


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(datetime(2026, 10, 2, 23, tzinfo=ZoneInfo(seed.timezone)))
    with TemporaryDirectory(prefix="si001-world-time-") as directory:
        path = Path(directory) / "smoke.db"
        store = SQLiteStore(path)
        world = WorldEntityService(store, clock)
        world.initialize_seed_entities(
            load_world_seed(root / "data/worlds/si_world.yaml")
        )
        home = world.resolve_place_name("住宅区的家")
        school = world.resolve_place_name("学校")
        life = CharacterLifeService(store, key, seed.initial_life_context, clock)
        life.update_life_context(key, current_location_entity_id=home)
        registry = NPCService(store, clock)
        home_npc = registry.create("Synthetic home NPC", place_entity_id=home)
        school_npc = registry.create("Synthetic school NPC", place_entity_id=school)
        time = WorldTimeService(seed.timezone, clock)
        observation = ObservationService(store, clock, world_time_service=time)
        before = dump(path)
        a = observation.observe(key)
        assert a.day_period == "night" and a.location_entity_id == home
        assert a.visible_npc_ids == (home_npc.npc_id,)
        print("23:00 local: night | observation at home")
        clock.advance(hours=8)
        snapshot = time.snapshot()
        morning = observation.observe(key)
        assert snapshot.day_period == morning.day_period == "morning"
        assert morning.location_entity_id == home
        assert morning.visible_npc_ids == a.visible_npc_ids
        assert dump(path) == before  # Includes all Memory/Action tables and timestamps.
        print("07:00 local: morning | Life/NPC/Memory/Action unchanged")
        # SQLiteStore uses per-operation connections that are already closed here;
        # there is no persistent Store connection or close() method.
        reopened = SQLiteStore(path)
        restarted_time = WorldTimeService(seed.timezone, clock)
        restarted_observation = ObservationService(
            reopened, clock, world_time_service=restarted_time
        )
        assert restarted_time.snapshot() == snapshot
        assert restarted_observation.observe(key) == morning
        assert dump(path) == before
        print("B8 restart with identical Clock: identical snapshot, no writes")

        # Combined World Foundation: explicit move, fresh observation, time, replay.
        resolver = ActionResolver(reopened, key, clock)
        action = MoveToIntent(
            action_id=uuid4(),
            character_id=key,
            destination_entity_id=school,
            expected_location_entity_id=home,
        )
        assert restarted_observation.observe(key).location_entity_id == home
        result = resolver.resolve(action)
        assert result.status == "success" and result.reason == "moved"
        b = restarted_observation.observe(key)
        assert b.location_entity_id == school
        assert b.visible_npc_ids == (school_npc.npc_id,) and b.day_period == "morning"
        after_move = dump(path)
        clock.advance(hours=15)
        night = restarted_observation.observe(key)
        assert night.day_period == "night" and night.location_entity_id == school
        assert dump(path) == after_move
        again = SQLiteStore(path)
        replay = ActionResolver(again, key, clock).resolve(action)
        assert replay.replayed and replay.status == "success"
        assert replay.resolved_at == result.resolved_at
        fresh = ObservationService(
            again, clock, world_time_service=WorldTimeService(seed.timezone, clock)
        ).observe(key)
        assert fresh == night
        assert dump(path) == after_move  # Replay did not move or refresh Life/receipt.
        assert registry.get(home_npc.npc_id) == home_npc
        assert registry.get(school_npc.npc_id) == school_npc
        assert again.list_active_memories() == ()
        print("B6+B7+B8: home -> school success | fresh observation | morning -> night")
        print(
            "Restart + old Action replay: historical result, no new movement or Memory"
        )
    assert not path.exists() and not Path(directory).exists()
    print("B8 and combined smoke passed; temporary database and directory removed.")


if __name__ == "__main__":
    main()
