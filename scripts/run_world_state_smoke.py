"""Offline static World identity / Life binding smoke; temporary SQLite only."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.clock import FixedClock
from evolving_companion.prompting import PromptBuilder
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    world_seed = load_world_seed(root / "data/worlds/si_world.yaml")
    clock = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    key = seed.identity.internal_id
    with TemporaryDirectory(prefix="si001-world-") as directory:
        path = Path(directory) / "smoke.db"
        store = SQLiteStore(path)
        world = WorldEntityService(store, clock)
        assert world.initialize_seed_entities(world_seed) == ()
        for entity in world.list_entities():
            print(f"{entity.canonical_name} | {entity.entity_type}")
        home = world.get_place(world.resolve_place_name("住宅区的家"))
        assert home.parent_entity_id == world.resolve_place_name("住宅区")
        life = CharacterLifeService(store, key, seed.initial_life_context, clock)
        state = CharacterStateService(store, clock).get_state(key)
        initial = life.get_life_context(key)
        assert initial.home_entity_id == home.entity_id
        assert initial.school_entity_id == world.resolve_place_name("学校")
        assert initial.primary_area_entity_id == world.resolve_place_name("教育生活区")
        assert initial.current_location_entity_id is None
        updated = life.update_life_context(
            key, current_location_entity_id=initial.school_entity_id
        )
        prompt = PromptBuilder(CharacterProjector().project(seed)).build(
            [], "你在哪？", character_life_context=life.project_context(key, world)
        )[0]["content"]
        print(prompt.split("【当前生活上下文】", 1)[1])
        for entity in world.list_entities():
            assert str(entity.entity_id) not in prompt
        assert "当前地点：学校" in prompt
        clock.advance(hours=12)
        assert life.get_life_context(key) == updated
        reopened = SQLiteStore(path)
        reopened_world = WorldEntityService(reopened, clock)
        assert reopened_world.initialize_seed_entities(world_seed) == ()
        assert reopened_world.list_entities() == world.list_entities()
        assert (
            CharacterLifeService(reopened, key, clock=clock).get_life_context(key)
            == updated
        )
        assert store.get_character_state(key) == state
        assert store.list_active_memories() == ()
        print("Parent, UUID binding, restart, +12h and State/Memory boundaries passed.")
    assert not path.exists()
    print("B4 World smoke passed; temporary database removed.")


if __name__ == "__main__":
    main()
