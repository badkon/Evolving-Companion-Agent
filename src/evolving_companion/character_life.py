"""Explicit, persisted Personal Life Context for one Character."""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from evolving_companion.clock import Clock, SystemClock

LifeStage = Literal["student", "worker", "unemployed", "unknown"]


class LifeContextData(BaseModel):
    """Minimal life fields; seed defaults bootstrap runtime only once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    life_stage: LifeStage = "unknown"
    home_reference: str | None = None
    school_reference: str | None = None
    primary_area_reference: str | None = None
    current_location_reference: str | None = None
    current_role: str | None = None

    @field_validator(
        "home_reference",
        "school_reference",
        "primary_area_reference",
        "current_location_reference",
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


class CharacterLifeStore(Protocol):
    def get_character_life_context(
        self, character_id: UUID
    ) -> CharacterLifeContext | None: ...

    def upsert_character_life_context(self, context: CharacterLifeContext) -> None: ...


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
            self._store.upsert_character_life_context(context)
        return context

    def update_life_context(
        self,
        character_id: UUID,
        *,
        life_stage: LifeStage | _Unchanged = UNCHANGED,
        home_reference: str | None | _Unchanged = UNCHANGED,
        school_reference: str | None | _Unchanged = UNCHANGED,
        primary_area_reference: str | None | _Unchanged = UNCHANGED,
        current_location_reference: str | None | _Unchanged = UNCHANGED,
        current_role: str | None | _Unchanged = UNCHANGED,
    ) -> CharacterLifeContext:
        current = self.get_life_context(character_id)
        values = current.model_dump()
        for name, value in (
            ("life_stage", life_stage),
            ("home_reference", home_reference),
            ("school_reference", school_reference),
            ("primary_area_reference", primary_area_reference),
            ("current_location_reference", current_location_reference),
            ("current_role", current_role),
        ):
            if value is not UNCHANGED:
                values[name] = value
        # Validate before comparing or persisting; invalid input never creates
        # a partially applied update.
        candidate = CharacterLifeContext.model_validate(values)
        if candidate == current:
            return current
        now = self._clock.now_utc()
        if now < current.updated_at:
            raise ValueError("clock_moved_backwards; Life Context update not applied")
        values["updated_at"] = now
        updated = CharacterLifeContext.model_validate(values)
        self._store.upsert_character_life_context(updated)
        return updated
