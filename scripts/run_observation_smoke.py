"""B6 offline smoke: synthetic NPCs in temporary SQLite, never runtime data."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_life import CharacterLifeService
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.npc import NPCService
from evolving_companion.observation import ObservationService
from evolving_companion.prompting import Message
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.calls.append(messages)
        return "在呢。"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    clock = FixedClock(datetime(2026, 10, 2, tzinfo=timezone.utc))
    with TemporaryDirectory(prefix="si001-observation-") as directory:
        path = Path(directory) / "smoke.db"
        store = SQLiteStore(path)
        world = WorldEntityService(store, clock)
        world.initialize_seed_entities(
            load_world_seed(root / "data/worlds/si_world.yaml")
        )
        life = CharacterLifeService(store, key, seed.initial_life_context, clock)
        life.get_life_context(key)
        school = world.resolve_place_name("学校")
        home = world.resolve_place_name("住宅区的家")
        registry = NPCService(store, clock)
        first = registry.create(
            "Synthetic A", place_entity_id=school, tags=("synthetic-a",)
        )
        second = registry.create(
            "Synthetic B", place_entity_id=home, tags=("synthetic-b",)
        )
        registry.create("Synthetic inactive", place_entity_id=school, active=False)
        observation = ObservationService(store, clock)
        life.update_life_context(key, current_location_entity_id=school)
        a = observation.observe(key)
        assert a.visible_npc_ids == (first.npc_id,) and a.place_name == "学校"
        print("Location A: school | anonymous presence: 1 | same-place filter: passed")
        client = FakeLLM()
        chat = Conversation(
            client,
            CharacterProjector().project(seed),
            store,
            character_id=key,
            clock=clock,
            character_life_service=life,
            observation_service=observation,
        )
        assert chat.send("你好") == "在呢。"
        clock.advance(minutes=1)
        life.update_life_context(key, current_location_entity_id=home)
        b = observation.observe(key)
        assert b.visible_npc_ids == (second.npc_id,) and b.place_name == "住宅区的家"
        assert chat.send("还在吗") == "在呢。"
        print("Location B: home | anonymous presence: 1 | fresh snapshot: passed")
        for call in client.calls:
            prompt = call[0]["content"]
            assert "【当前可观察环境】" in prompt and "1位匿名人物" in prompt
            for npc in (first, second):
                assert npc.canonical_name not in prompt
                assert str(npc.npc_id) not in prompt
                assert all(tag not in prompt for tag in npc.tags)
        assert '当前地点："学校"' not in client.calls[-1][0]["content"]
        assert len(client.calls) == 2 and store.list_active_memories() == ()
        print("Prompt anonymity / no extra model calls / no Memory writes: passed")
        # All Store operations close their own connections, including capture_context.
    assert not path.exists() and not Path(directory).exists()
    print("B6 observation smoke passed; temporary database and directory removed.")


if __name__ == "__main__":
    main()
