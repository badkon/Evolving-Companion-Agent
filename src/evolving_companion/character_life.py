"""Explicit, persisted Personal Life Context for one Character."""

from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from evolving_companion.clock import Clock, SystemClock
from evolving_companion.world import WorldEntity, WorldEntityService

LifeStage = Literal["student", "worker", "unemployed", "unknown"]


class LifeContextData(BaseModel):
    """Minimal life fields; seed defaults bootstrap runtime only once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    life_stage: LifeStage = "unknown"
    home_entity_id: UUID | None = None
    school_entity_id: UUID | None = None
    primary_area_entity_id: UUID | None = None
    current_location_entity_id: UUID | None = None
    current_role: str | None = None

    @field_validator(
        "current_role",
    )
    @classmethod
    def require_nonempty_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("life reference/role must not be empty; use None to clear")
        return value


class CharacterLifeContext(LifeContextData):
    # 绑定 identity.internal_id；地点是粗粒度引用，不是 World 实体或坐标。
    character_id: UUID
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class _Unchanged(Enum):
    token = "unchanged"


UNCHANGED = _Unchanged.token


@dataclass(frozen=True)
class ProjectedLifeContext:
    """Display-only context: no World metadata or authoritative identity fields."""

    life_stage: LifeStage
    current_role: str | None
    home_reference: str | None
    school_reference: str | None
    primary_area_reference: str | None
    current_location_reference: str | None


class CharacterLifeStore(Protocol):
    def get_world_entity(self, entity_id: UUID) -> WorldEntity | None: ...
    def get_character_life_context(
        self, character_id: UUID
    ) -> CharacterLifeContext | None: ...

    def initialize_character_life_context(
        self, context: CharacterLifeContext
    ) -> CharacterLifeContext: ...

    def patch_character_life_context(
        self, character_id: UUID, changes: LifeContextData, now: datetime
    ) -> CharacterLifeContext: ...


class CharacterLifeService:
    """Read once-initialized context and apply explicit partial updates."""

    def __init__(
        self,
        store: CharacterLifeStore,
        character_id: UUID,
        initial_context: LifeContextData | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(character_id, UUID):
            raise TypeError("character_id must be identity.internal_id (UUID)")
        self._store = store
        self._character_id = character_id
        self._initial_context = initial_context or LifeContextData()
        self._clock = clock or SystemClock()

    def get_life_context(self, character_id: UUID) -> CharacterLifeContext:
        if character_id != self._character_id:
            raise ValueError(
                "character_id does not match configured identity.internal_id"
            )
        context = self._store.get_character_life_context(character_id)
        if context is None:
            context = CharacterLifeContext.model_validate(
                {
                    **self._initial_context.model_dump(),
                    "character_id": character_id,
                    "updated_at": self._clock.now_utc(),
                }
            )
            context = self._store.initialize_character_life_context(context)
        return context

    def project_context(
        self, character_id: UUID, world: WorldEntityService
    ) -> ProjectedLifeContext:
        context = self.get_life_context(character_id)

        def name(value: UUID | None) -> str | None:
            return world.resolve_display_name(value) if value is not None else None

        return ProjectedLifeContext(
            context.life_stage,
            context.current_role,
            name(context.home_entity_id),
            name(context.school_entity_id),
            name(context.primary_area_entity_id),
            name(context.current_location_entity_id),
        )

    def update_life_context(
        self,
        character_id: UUID,
        *,
        life_stage: LifeStage | _Unchanged = UNCHANGED,
        home_entity_id: UUID | None | _Unchanged = UNCHANGED,
        school_entity_id: UUID | None | _Unchanged = UNCHANGED,
        primary_area_entity_id: UUID | None | _Unchanged = UNCHANGED,
        current_location_entity_id: UUID | None | _Unchanged = UNCHANGED,
        current_role: str | None | _Unchanged = UNCHANGED,
    ) -> CharacterLifeContext:
        # Preserve existing bootstrap semantics, but never write this read's row
        # back. The Store reads the latest row under the same lock as its patch.
        self.get_life_context(character_id)
        values: dict[str, object] = {}
        for name, value in (
            ("life_stage", life_stage),
            ("home_entity_id", home_entity_id),
            ("school_entity_id", school_entity_id),
            ("primary_area_entity_id", primary_area_entity_id),
            ("current_location_entity_id", current_location_entity_id),
            ("current_role", current_role),
        ):
            if value is not UNCHANGED:
                values[name] = value
        changes = LifeContextData.model_validate(values)
        return self._store.patch_character_life_context(
            character_id, changes, self._clock.now_utc()
        )
