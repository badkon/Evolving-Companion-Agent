"""Offline smoke test for the B1 Character Time Model."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.clock import FixedClock
from evolving_companion.conversation import Conversation
from evolving_companion.prompting import Message
from evolving_companion.storage import SQLiteStore
from evolving_companion.time_model import CharacterTimeService


class _Reply:
    def complete(self, messages: list[Message]) -> str:
        return "在呢。"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    t0 = datetime(2026, 9, 30, tzinfo=timezone.utc)
    clock = FixedClock(t0)
    with TemporaryDirectory(prefix="si001-time-", dir=root) as directory:
        path = Path(directory) / "smoke.db"
        store = SQLiteStore(path)
        state_service = CharacterStateService(store, clock)
        state = state_service.get_state(seed.identity.internal_id)
        state_service.save_state(
            state.model_copy(update={"energy": "low", "updated_at": t0})
        )
        time_service = CharacterTimeService(
            store, seed.identity.internal_id, seed.timezone, clock
        )
        assert time_service.snapshot().offline_duration is None
        time_service.record_successful_interaction()
        clock.advance(hours=8)
        snap = time_service.snapshot()
        assert snap.offline_duration == timedelta(hours=8)
        conversation = Conversation(
            _Reply(),
            CharacterProjector().project(seed),
            store,
            character_state_service=state_service,
            character_id=seed.identity.internal_id,
            character_timezone=seed.timezone,
            clock=clock,
        )
        conversation.send("还醒着吗？")
        assert state_service.get_state(seed.identity.internal_id).energy == "medium"
        anchor = store.get_last_interaction_at(seed.identity.internal_id)
        clock.set(t0 - timedelta(hours=1))
        rolled_back = time_service.snapshot()
        assert rolled_back.offline_duration == timedelta(0)
        assert rolled_back.diagnostics == ("clock_moved_backwards",)
        reopened = SQLiteStore(path)
        assert reopened.get_last_interaction_at(seed.identity.internal_id) == anchor
        print(
            "B1 Time Model smoke passed: timezone, offline duration, State elapsed, rollback, persistence."
        )


if __name__ == "__main__":
    main()
