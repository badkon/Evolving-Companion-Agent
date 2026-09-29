"""Validated seed data for the current Character."""

from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SeedModel(BaseModel):
    """Shared strict and immutable settings for seed data models."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class IdentityData(SeedModel):
    internal_id: UUID
    development_id: str
    working_name: str
    personal_name: str | None
    identity_stage: str
    continuity_generation: int = Field(gt=0)


class SocialTendencies(SeedModel):
    strangers: str
    familiar_people: str
    solitude: str


class PersonalityData(SeedModel):
    baseline_traits: tuple[str, ...]
    social_tendencies: SocialTendencies


class BehavioralBoundaries(SeedModel):
    generic_assistant_role: bool
    automatic_obedience: bool
    automatic_agreement: bool
    automatic_affection: bool
    automatic_trust: bool
    always_gentle: bool


class KnowledgeBoundaries(SeedModel):
    model_knowledge_is_character_knowledge: bool
    model_capability_is_character_capability: bool
    origin_records_are_lived_memory: bool
    fabricated_past_allowed: bool


class SeedPreferences(SeedModel):
    likes: tuple[str, ...]
    dislikes: tuple[str, ...]


class SeedCapabilities(SeedModel):
    can: tuple[str, ...]
    cannot: tuple[str, ...]


class CharacterSeedData(SeedModel):
    schema_version: Literal["0.1"]
    identity: IdentityData
    personality: PersonalityData
    behavioral_boundaries: BehavioralBoundaries
    knowledge_boundaries: KnowledgeBoundaries
    seed_preferences: SeedPreferences
    seed_capabilities: SeedCapabilities


def load_character_seed_data(path: str | Path) -> CharacterSeedData:
    """Read and validate a Character Seed YAML file."""
    with Path(path).open(encoding="utf-8") as file:
        raw_data = yaml.safe_load(file)
    return CharacterSeedData.model_validate(raw_data)
