from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import load_character_seed_data
from evolving_companion.character_state import CharacterState, CharacterStateService
from evolving_companion.character_projection import ProjectedCharacterContext
from evolving_companion.conversation import Conversation, TextCompletionClient
from evolving_companion.prompting import Message, PromptBuilder
from evolving_companion.storage import SQLiteStore


def character_id() -> UUID:
    seed_path = Path(__file__).resolve().parents[1] / "data/characters/si_001.yaml"
    return load_character_seed_data(seed_path).identity.internal_id


def test_state_defaults_and_persists_by_internal_uuid(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    key = character_id()
    state = CharacterStateService(SQLiteStore(path)).get_state(key)

    assert state.character_id == key
    assert (state.energy, state.attention) == ("medium", "normal")
    assert (state.mood_tendency, state.social_engagement) == ("neutral", "normal")
    assert state.current_activity is None

    reopened = CharacterStateService(SQLiteStore(path)).get_state(key)
    assert reopened == state
    assert UUID(str(reopened.character_id)) == key


def test_state_partial_update_and_explicit_activity_clear(tmp_path: Path) -> None:
    service = CharacterStateService(SQLiteStore(tmp_path / "state.db"))
    key = character_id()
    initial = service.get_state(key)
    changed = service.update_state(key, energy="low", current_activity="看漫画")
    assert changed.energy == "low"
    assert changed.attention == initial.attention
    assert changed.current_activity == "看漫画"
    assert changed.updated_at >= initial.updated_at

    cleared = service.update_state(key, current_activity=None)
    assert cleared.current_activity is None
    assert cleared.energy == "low"


def test_state_rejects_invalid_enum_value(tmp_path: Path) -> None:
    state = CharacterStateService(SQLiteStore(tmp_path / "state.db")).get_state(
        character_id()
    )
    values = state.model_dump()
    values["energy"] = "extreme"
    with pytest.raises(ValidationError):
        CharacterState.model_validate(values)


def test_prompt_adds_separate_concise_state_without_metadata(tmp_path: Path) -> None:
    key = character_id()
    state = CharacterStateService(SQLiteStore(tmp_path / "state.db")).update_state(
        key, energy="low", current_activity="看漫画"
    )
    system = PromptBuilder(ProjectedCharacterContext("玲的人格资料")).build(
        [], "你好", character_state=state
    )[0]["content"]

    assert "【关于玲】\n玲的人格资料" in system
    assert "【玲当前状态】" in system
    assert "精力：偏低" in system
    assert "当前活动：看漫画" in system
    assert "updated_at" not in system
    assert str(key) not in system
    assert "不代表人格、身份或长期记忆" in system


def test_conversation_reads_state_but_does_not_update_it(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "state.db")
    state_service = CharacterStateService(store)
    key = character_id()
    before = state_service.update_state(key, energy="low")

    class InspectingClient(TextCompletionClient):
        def complete(self, messages: list[Message]) -> str:
            system = messages[0]["content"]
            assert "【玲当前状态】" in system
            assert "精力：偏低" in system
            return "好。"

    conversation = Conversation(
        InspectingClient(),
        ProjectedCharacterContext("玲"),
        store,
        character_state_service=state_service,
        character_id=key,
    )
    assert conversation.send("你好") == "好。"
    after = CharacterStateService(SQLiteStore(tmp_path / "state.db")).get_state(key)
    assert after == before
