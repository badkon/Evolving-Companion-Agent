"""Offline smoke for explicit Personal Life Context persistence."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
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
    key = seed.identity.internal_id
    clock = FixedClock(datetime(2026, 9, 30, tzinfo=timezone.utc))
    with TemporaryDirectory(prefix="si001-personal-life-") as directory:
        path = Path(directory) / "smoke.db"
        store = SQLiteStore(path)
        world = WorldEntityService(store, clock)
        world.initialize_seed_entities(
            load_world_seed(root / "data/worlds/si_world.yaml")
        )
        state = CharacterStateService(store, clock).get_state(key)
        life = CharacterLifeService(store, key, seed.initial_life_context, clock)
        initial = life.get_life_context(key)
        for name in (
            "life_stage",
            "current_role",
            "home_entity_id",
            "school_entity_id",
            "primary_area_entity_id",
            "current_location_entity_id",
        ):
            print(f"{name}: {getattr(initial, name)}")
        assert initial.life_stage == "student"
        assert initial.current_location_entity_id is None
        clock.advance(hours=1)
        updated = life.update_life_context(
            key, current_location_entity_id=world.resolve_place_name("学校")
        )
        assert life.get_life_context(key) == updated
        reopened = CharacterLifeService(
            SQLiteStore(path), key, seed.initial_life_context, clock
        )
        assert reopened.get_life_context(key) == updated
        clock.advance(hours=12)
        assert reopened.get_life_context(key) == updated
        prompt = PromptBuilder(CharacterProjector().project(seed)).build(
            [],
            "你在哪里？",
            character_life_context=reopened.project_context(key, world),
        )[0]["content"]
        assert "【当前生活上下文】" in prompt and "当前地点：学校" in prompt
        assert str(key) not in prompt and "updated_at" not in prompt
        assert store.get_character_state(key) == state
        with closing(sqlite3.connect(path)) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
            )
        print(
            "Location update persisted across restart and +12h; Prompt projection passed."
        )
        print("Character State unchanged; no Memory was created.")
    assert not path.exists()
    print("B3 Personal Life smoke passed; temporary database removed.")


if __name__ == "__main__":
    main()
