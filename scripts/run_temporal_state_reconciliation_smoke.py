"""Offline B2 smoke using an injected clock and a temporary SQLite database."""

from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta, timezone

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_state import CharacterStateService
from evolving_companion.character_state_transition import (
    CharacterStateTransitionService,
)
from evolving_companion.clock import FixedClock

from evolving_companion.storage import SQLiteStore


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(root / "data/characters/si_001.yaml")
    key = seed.identity.internal_id
    t0 = datetime(2026, 9, 30, tzinfo=timezone.utc)
    clock = FixedClock(t0)
    with TemporaryDirectory(prefix="si001-temporal-state-") as directory:
        path = Path(directory) / "smoke.db"
        states = CharacterStateService(SQLiteStore(path), clock)
        transitions = CharacterStateTransitionService(states, clock)
        initial = states.update_state(
            key,
            energy="low",
            attention="focused",
            mood_tendency="positive",
            social_engagement="engaged",
            current_activity="看漫画",
        )
        clock.advance(minutes=30)
        assert transitions.apply_elapsed_time(key).after_state == initial
        print("T0+30min: unchanged; original anchors retained.")
        clock.set(t0 + timedelta(hours=3))
        three = transitions.apply_elapsed_time(key).after_state
        assert three.energy == "medium" and three.attention == "normal"
        assert (
            three.mood_tendency == "positive" and three.social_engagement == "engaged"
        )
        assert three.mood_updated_at == three.social_updated_at == t0
        print("T0+3h: energy=medium, attention=normal; mood/social anchors still T0.")
        states = CharacterStateService(SQLiteStore(path), clock)
        transitions = CharacterStateTransitionService(states, clock)
        clock.set(t0 + timedelta(hours=6))
        six = transitions.apply_elapsed_time(key).after_state
        assert six.mood_tendency == "neutral" and six.social_engagement == "normal"
        print("T0+6h: mood=neutral, social=normal after reopening SQLite.")
        clock.set(t0 + timedelta(hours=24))
        day = transitions.apply_elapsed_time(key).after_state
        assert day.current_activity == "看漫画"
        assert day == six
        print("T0+24h: activity preserved; no-change check leaves anchors intact.")
        clock.set(t0 - timedelta(hours=1))
        rollback = transitions.apply_elapsed_time(key)
        assert rollback.after_state == day
        assert rollback.diagnostics == ("clock_moved_backwards",)
        print("Clock rollback: all fields and timestamps preserved.")
    assert not path.exists()
    print("B2 temporal reconciliation smoke passed; temporary database removed.")


if __name__ == "__main__":
    main()
