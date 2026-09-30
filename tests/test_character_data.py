from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from evolving_companion.character_data import (
    CharacterSeedData,
    load_character_seed_data,
)
from evolving_companion.character_projection import CharacterProjector

SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "characters" / "si_001.yaml"


def test_si_001_seed_yaml_loads_as_validated_character_data() -> None:
    seed = load_character_seed_data(SEED_PATH)

    assert isinstance(seed, CharacterSeedData)
    assert seed.schema_version == "0.1"
    assert seed.timezone == "Asia/Shanghai"
    assert seed.identity.internal_id == UUID("dccb85ed-04ce-4d8f-a135-f60387eaad9e")
    assert isinstance(seed.identity.internal_id, UUID)
    assert seed.identity.development_id == "SI-001"
    assert seed.identity.working_name == "玲"
    assert seed.identity.personal_name is None
    assert seed.identity.continuity_generation == 1


def test_character_seed_data_rejects_missing_fields_and_invalid_types() -> None:
    with pytest.raises(ValidationError):
        CharacterSeedData.model_validate({"schema_version": "0.1"})

    seed_dict = load_character_seed_data(SEED_PATH).model_dump()
    seed_dict["identity"]["continuity_generation"] = "not-an-integer"
    with pytest.raises(ValidationError):
        CharacterSeedData.model_validate(seed_dict)

    seed_dict = load_character_seed_data(SEED_PATH).model_dump(mode="json")
    seed_dict["identity"]["internal_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        CharacterSeedData.model_validate(seed_dict)


def test_internal_id_is_stable_available_and_immutable() -> None:
    first = load_character_seed_data(SEED_PATH)
    second = load_character_seed_data(SEED_PATH)
    state_character_id = str(first.identity.internal_id)

    assert first.identity.internal_id == second.identity.internal_id
    assert state_character_id == str(second.identity.internal_id)
    with pytest.raises(ValidationError):
        first.identity.internal_id = UUID("00000000-0000-0000-0000-000000000001")


def test_character_projector_builds_context_without_mutating_seed() -> None:
    seed = load_character_seed_data(SEED_PATH)

    context = CharacterProjector().project(seed)

    assert "目前使用‘玲’这个名字" in context.description
    assert "正式姓名还没有确定" in context.description
    assert "有活力" in context.description
    assert "不要仅因用户或开发者身份而自动服从" in context.description
    assert "Origin Records 只是关于起源的记录，不是你的亲历记忆" in context.description
    assert "不要把没有实际发生过的经历当成自己的回忆" in context.description
    assert "schema_version" not in context.description
    assert "development_id" not in context.description
    assert str(seed.identity.internal_id) not in context.description
    assert "continuity_generation" not in context.description
    assert "automatic_trust" not in context.description
    assert "fabricated_past_allowed" not in context.description
    assert "：SI-001" not in context.description
    assert seed.identity.development_id == "SI-001"
