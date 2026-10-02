"""B7 explicit actions, durable replay and observation in temporary SQLite."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.clock import FixedClock
from evolving_companion.npc import NPCService
from evolving_companion.observation import ObservationService
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed
from evolving_companion.world_actions import ActionResolver, MoveToIntent


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(datetime(2026, 10, 2, tzinfo=timezone.utc))
    with TemporaryDirectory(prefix="si001-world-action-") as directory:
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
        observation = ObservationService(store, clock)
        a = observation.observe(key)
        assert a.location_entity_id == home and a.visible_npc_ids == (home_npc.npc_id,)
        resolver = ActionResolver(store, key, clock)
        action_a = MoveToIntent(
            action_id=uuid4(),
            character_id=key,
            destination_entity_id=school,
            expected_location_entity_id=home,
        )
        clock.advance(minutes=1)
        result_a = resolver.resolve(action_a)
        assert result_a.status == "success" and result_a.reason == "moved"
        b = observation.observe(key)
        assert b.location_entity_id == school and b.visible_npc_ids == (
            school_npc.npc_id,
        )
        print("Home -> school: success / moved | fresh school observation: passed")
        before = life.get_life_context(key)
        invalid = resolver.resolve(
            MoveToIntent(
                action_id=uuid4(),
                character_id=key,
                destination_entity_id=UUID(int=999),
                expected_location_entity_id=school,
            )
        )
        assert invalid.status == "rejected" and invalid.reason == "invalid_destination"
        assert life.get_life_context(key) == before
        print("Invalid destination: rejected | Life unchanged: passed")
        clock.advance(minutes=1)
        assert (
            resolver.resolve(
                MoveToIntent(
                    action_id=uuid4(),
                    character_id=key,
                    destination_entity_id=home,
                    expected_location_entity_id=school,
                )
            ).status
            == "success"
        )
        before_replay = life.get_life_context(key)
        # Store operations close all connections; reopening retains receipts only.
        reopened = SQLiteStore(path)
        restarted = ActionResolver(reopened, key, clock)
        clock.advance(minutes=1)
        replay = restarted.resolve(action_a)
        assert replay.replayed and replay.status == "success"
        assert (
            replay.resolved_at == result_a.resolved_at
            and replay.location_after == school
        )
        assert reopened.get_character_life_context(key) == before_replay
        assert (
            ObservationService(reopened, clock).observe(key).location_entity_id == home
        )
        assert store.list_active_memories() == ()
        print(
            "A/B/A replay after restart: historical school receipt | current home preserved"
        )
    assert not path.exists() and not Path(directory).exists()
    print("B7 world action smoke passed; temporary database and directory removed.")


if __name__ == "__main__":
    main()
