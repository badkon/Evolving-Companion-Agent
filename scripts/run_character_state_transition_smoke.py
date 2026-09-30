"""Offline smoke check for deterministic Character State transitions."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import (
    CharacterState,
    CharacterStateService,
    TEMPORAL_ANCHORS,
)
from evolving_companion.character_state_transition import (
    CharacterStateEvent,
    CharacterStateTransitionService,
)
from evolving_companion.llm import LLMClient
from evolving_companion.local_env import load_local_env
from evolving_companion.prompting import PromptBuilder
from evolving_companion.storage import SQLiteStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="可选地请求真实 LLM 观察状态 Prompt 的表达效果",
    )
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(
        repository_root / "data" / "characters" / "si_001.yaml"
    )
    character_id: UUID = seed.identity.internal_id
    base_time = datetime.now(timezone.utc)
    with TemporaryDirectory(prefix="si001-state-transition-") as directory:
        database_path = Path(directory) / "state.db"
        store = SQLiteStore(database_path)
        states = CharacterStateService(store)
        transitions = CharacterStateTransitionService(states)
        initial = states.get_state(character_id)
        states.save_state(
            CharacterState.model_validate(
                initial.model_dump(exclude=set(TEMPORAL_ANCHORS.values()))
                | {
                    "energy": "low",
                    "attention": "focused",
                    "mood_tendency": "neutral",
                    "social_engagement": "normal",
                    "updated_at": base_time,
                }
            )
        )

        four_hours_later = base_time + timedelta(hours=4)
        elapsed_result = transitions.apply_elapsed_time(character_id, four_hours_later)
        assert elapsed_result.after_state.energy == "medium"
        assert elapsed_result.after_state.attention == "normal"

        transitions.apply_event(
            character_id,
            CharacterStateEvent("mood_up", occurred_at=four_hours_later),
        )
        transitions.apply_event(
            character_id,
            CharacterStateEvent("social_engagement_up", occurred_at=four_hours_later),
        )
        transitions.apply_event(
            character_id,
            CharacterStateEvent(
                "activity_set",
                occurred_at=four_hours_later,
                activity_name="看漫画",
            ),
        )
        changed = states.get_state(character_id)
        assert changed.mood_tendency == "positive"
        assert changed.social_engagement == "engaged"
        assert changed.current_activity == "看漫画"

        twelve_hours_later = base_time + timedelta(hours=12)
        later = transitions.apply_elapsed_time(character_id, twelve_hours_later)
        assert later.after_state.energy == "medium"
        persisted = CharacterStateService(SQLiteStore(database_path)).get_state(
            character_id
        )
        assert persisted == later.after_state
        print("Elapsed-time, explicit-event, and SQLite persistence checks passed.")
        print(
            "Final state: "
            f"energy={persisted.energy}, attention={persisted.attention}, "
            f"mood={persisted.mood_tendency}, "
            f"social={persisted.social_engagement}, "
            f"activity={persisted.current_activity}"
        )

        if args.real_llm:
            if not load_local_env(repository_root / ".env.local"):
                raise SystemExit(
                    "DEEPSEEK_API_KEY is not configured. Set it in the environment "
                    "or create .env.local from .env.example."
                )
            prompt = PromptBuilder(CharacterProjector().project(seed)).build(
                [], "玲，你现在状态怎么样？", character_state=persisted
            )
            print(f"玲：{LLMClient().complete(prompt)}")


if __name__ == "__main__":
    main()
