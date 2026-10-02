"""Offline Tier-0 NPC smoke using synthetic records and temporary SQLite."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.clock import FixedClock
from evolving_companion.npc import NPCService
from evolving_companion.storage import SQLiteStore
from evolving_companion.world import WorldEntityService, load_world_seed


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    clock = FixedClock(datetime(2026, 10, 2, tzinfo=timezone.utc))
    with TemporaryDirectory(prefix="si001-npc-") as directory:
        path = Path(directory) / "smoke.db"
        store = SQLiteStore(path)
        world = WorldEntityService(store, clock)
        world.initialize_seed_entities(
            load_world_seed(root / "data/worlds/si_world.yaml")
        )
        service = NPCService(store, clock)
        npc = service.create(
            "Synthetic smoke NPC", place_entity_id=world.resolve_place_name("学校")
        )
        assert service.get(npc.npc_id) == npc
        assert npc.place_entity_id is not None
        print(f"NPC count: 1 | active count: {len(service.list_active())}")
        print(f"Initial location: {world.resolve_display_name(npc.place_entity_id)}")
        clock.advance(hours=1)
        moved = service.set_location(npc.npc_id, world.resolve_place_name("住宅区"))
        assert moved.place_entity_id is not None
        print(f"Updated location: {world.resolve_display_name(moved.place_entity_id)}")
        inactive = service.set_active(npc.npc_id, False)
        reopened = NPCService(SQLiteStore(path), clock)
        assert reopened.get(npc.npc_id) == inactive
        assert reopened.list_active() == ()
        assert store.list_active_memories() == ()
        print("NPC count: 1 | active count: 0 | persistence: passed")
        # SQLiteStore closes each operation's connection; no live DB handles.
    assert not path.exists()
    print("B5 NPC smoke passed; temporary database removed.")


if __name__ == "__main__":
    main()
