"""Offline smoke test for explicit Character Events and State transitions."""

from pathlib import Path
from tempfile import TemporaryDirectory

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_events import (
    ActivityEventPayload,
    CharacterEvent,
    CharacterEventService,
)
from evolving_companion.character_state import CharacterStateService
from evolving_companion.character_state_transition import (
    CharacterStateTransitionService,
)
from evolving_companion.storage import SQLiteStore


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(
        repository_root / "data" / "characters" / "si_001.yaml"
    )
    character_id = seed.identity.internal_id

    with TemporaryDirectory(prefix="si001-character-event-") as directory:
        database_path = Path(directory) / "events.db"
        store = SQLiteStore(database_path)
        states = CharacterStateService(store)
        transitions = CharacterStateTransitionService(states)
        events = CharacterEventService(character_id, states, transitions)
        states.update_state(character_id, energy="low", attention="normal")

        activity = events.handle(
            CharacterEvent(
                character_id=character_id,
                event_type="activity_started",
                source="activity",
                payload=ActivityEventPayload(activity_name="看漫画"),
            )
        )
        assert activity.after_state is not None
        assert activity.after_state.current_activity == "看漫画"

        focus = events.handle(
            CharacterEvent(
                character_id=character_id,
                event_type="focused_task_started",
                source="activity",
                payload=ActivityEventPayload(activity_name="整理资料"),
            )
        )
        assert focus.after_state is not None
        assert focus.after_state.attention == "focused"
        assert focus.after_state.current_activity == "整理资料"

        stale_activity_end = events.handle(
            CharacterEvent(
                character_id=character_id,
                event_type="activity_ended",
                source="activity",
                payload=ActivityEventPayload(activity_name="看漫画"),
            )
        )
        assert not stale_activity_end.handled
        assert stale_activity_end.after_state is not None
        assert stale_activity_end.after_state.current_activity == "整理资料"

        focus_end = events.handle(
            CharacterEvent(
                character_id=character_id,
                event_type="focused_task_ended",
                source="activity",
                payload=ActivityEventPayload(activity_name="整理资料"),
            )
        )
        assert focus_end.after_state is not None
        assert focus_end.after_state.attention == "normal"

        rest = events.handle(
            CharacterEvent(
                character_id=character_id,
                event_type="rest_started",
                source="developer",
            )
        )
        assert rest.after_state is not None
        assert rest.after_state.energy == "medium"

        reopened = CharacterStateService(SQLiteStore(database_path)).get_state(
            character_id
        )
        assert reopened == rest.after_state
        assert reopened.current_activity == "整理资料"
        assert store.list_active_memories() == ()
        print("Explicit Character Event smoke checks passed.")
        print(
            "Persisted state: "
            f"energy={reopened.energy}, attention={reopened.attention}, "
            f"activity={reopened.current_activity}"
        )


if __name__ == "__main__":
    main()
