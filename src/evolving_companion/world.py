"""Static Place identity, seed initialization and exact name resolution."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from evolving_companion.clock import Clock, SystemClock


class WorldEntitySeed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: UUID
    entity_type: Literal["place"] = "place"
    canonical_name: str
    parent_entity_id: UUID | None = None
    description: str | None = None

    @field_validator("canonical_name")
    @classmethod
    def require_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("canonical_name must not be empty")
        return value


class WorldEntity(WorldEntitySeed):
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("World timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class WorldSeedData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entities: tuple[WorldEntitySeed, ...]

    @model_validator(mode="after")
    def validate_parents(self) -> "WorldSeedData":
        by_id = {entity.entity_id: entity for entity in self.entities}
        if len(by_id) != len(self.entities):
            raise ValueError("duplicate seed entity_id")
        for entity in self.entities:
            seen = {entity.entity_id}
            parent = entity.parent_entity_id
            while parent is not None:
                if parent not in by_id:
                    raise ValueError("seed parent does not exist")
                if parent in seen:
                    raise ValueError("cyclic seed containment")
                seen.add(parent)
                parent = by_id[parent].parent_entity_id
        return self


def load_world_seed(path: str | Path) -> WorldSeedData:
    with Path(path).open(encoding="utf-8") as file:
        return WorldSeedData.model_validate(yaml.safe_load(file))


class WorldStore(Protocol):
    def get_world_entity(self, entity_id: UUID) -> WorldEntity | None: ...
    def list_world_entities(self) -> tuple[WorldEntity, ...]: ...
    def insert_world_seed(self, entities: tuple[WorldEntity, ...]) -> None: ...
    def migrate_life_references(
        self, names: dict[str, tuple[UUID, ...]]
    ) -> tuple[str, ...]: ...


class WorldEntityService:
    def __init__(self, store: WorldStore, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    def initialize_seed_entities(self, seed: WorldSeedData) -> tuple[str, ...]:
        now = self._clock.now_utc()
        entities = tuple(
            WorldEntity.model_validate(
                {**entity.model_dump(), "created_at": now, "updated_at": now}
            )
            for entity in seed.entities
        )
        self._store.insert_world_seed(entities)
        # Migration uses only exact seed names, never fuzzy matches or runtime guesses.
        names: dict[str, tuple[UUID, ...]] = {}
        for entity in seed.entities:
            names[entity.canonical_name] = (
                *names.get(entity.canonical_name, ()),
                entity.entity_id,
            )
        return self._store.migrate_life_references(names)

    def get_entity(self, entity_id: UUID) -> WorldEntity | None:
        return self._store.get_world_entity(entity_id)

    def list_entities(self) -> tuple[WorldEntity, ...]:
        return self._store.list_world_entities()

    def get_place(self, entity_id: UUID) -> WorldEntity:
        entity = self.get_entity(entity_id)
        if entity is None or entity.entity_type != "place":
            raise ValueError("Unknown Place entity_id")
        return entity

    def resolve_display_name(self, entity_id: UUID) -> str:
        return self.get_place(entity_id).canonical_name

    def resolve_place_name(self, name: str) -> UUID:
        matches = tuple(
            entity
            for entity in self.list_entities()
            if entity.entity_type == "place" and entity.canonical_name == name
        )
        if len(matches) > 1:
            raise ValueError("ambiguous Place name; use /life location-id <uuid>")
        if not matches:
            raise ValueError("Unknown Place name")
        return matches[0].entity_id
