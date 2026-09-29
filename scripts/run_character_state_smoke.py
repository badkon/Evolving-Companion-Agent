"""Offline smoke test for SQLite-backed Character State v0.1."""

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_projection import CharacterProjector
from evolving_companion.character_state import CharacterStateService
from evolving_companion.prompting import PromptBuilder
from evolving_companion.storage import SQLiteStore


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    seed = load_character_seed_data(
        repository_root / "data" / "characters" / "si_001.yaml"
    )
    character_id: UUID = seed.identity.internal_id
    with TemporaryDirectory(prefix="si001-character-state-") as directory:
        database_path = Path(directory) / "state.db"
        service = CharacterStateService(SQLiteStore(database_path))
        initial = service.get_state(character_id)
        assert initial.energy == "medium"
        updated = service.update_state(
            character_id,
            energy="low",
            attention="focused",
            current_activity="看漫画",
        )
        persisted = CharacterStateService(SQLiteStore(database_path)).get_state(
            character_id
        )
        assert persisted == updated

        context = CharacterProjector().project(seed)
        prompt = PromptBuilder(context).build(
            [], "今天状态怎么样？", character_state=persisted
        )
        system = prompt[0]["content"]
        assert "【玲当前状态】" in system
        assert "【可参考的长期记忆候选】" not in system
        print(f"Temporary database: {database_path}")
        print(
            "State defaults, explicit update, restart persistence, and prompt passed."
        )
        cleared = service.update_state(
            character_id, energy="high", current_activity=None
        )
        assert cleared.current_activity is None and cleared.energy == "high"
        print("Explicit partial update and activity clear passed.")


if __name__ == "__main__":
    main()
