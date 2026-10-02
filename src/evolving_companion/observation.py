"""Read-only, turn-local observation; not Knowledge, Experience or Memory."""

from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from evolving_companion.character_life import ProjectedLifeContext
from evolving_companion.clock import Clock, SystemClock

MAX_VISIBLE_NPCS = 20


class ObservationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    character_id: UUID
    observed_at: datetime
    location_entity_id: UUID | None = None
    place_name: str | None = None
    # 内部引用，不代表认识这些人物，也不投影身份信息。
    visible_npc_ids: tuple[UUID, ...] = ()
    truncated: bool = False
    status: Literal["available", "unknown_location"] = "unknown_location"

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_context(self) -> "ObservationSnapshot":
        if len(self.visible_npc_ids) > MAX_VISIBLE_NPCS:
            raise ValueError("observation exceeds context limit")
        if self.status == "unknown_location":
            if (
                self.location_entity_id is not None
                or self.place_name is not None
                or self.visible_npc_ids
                or self.truncated
            ):
                raise ValueError("unknown location cannot supply environment facts")
        elif (
            self.location_entity_id is None
            or not self.place_name
            or not self.place_name.strip()
        ):
            raise ValueError("available observation requires a Place")
        return self


class ObservationStore(Protocol):
    def capture_observation_context(
        self, character_id: UUID, observed_at: datetime
    ) -> tuple[ProjectedLifeContext | None, ObservationSnapshot]: ...


class ObservationContextPort(Protocol):
    def capture_context(
        self, character_id: UUID, now_utc: datetime | None = None
    ) -> tuple[ProjectedLifeContext | None, ObservationSnapshot]: ...


class ObservationService:
    def __init__(self, store: ObservationStore, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    def capture_context(
        self, character_id: UUID, now_utc: datetime | None = None
    ) -> tuple[ProjectedLifeContext | None, ObservationSnapshot]:
        if not isinstance(character_id, UUID):
            raise TypeError("character_id must be identity.internal_id (UUID)")
        # Validate the shared turn timestamp before any database read.
        stamp = ObservationSnapshot(
            character_id=character_id,
            observed_at=now_utc if now_utc is not None else self._clock.now_utc(),
        ).observed_at
        return self._store.capture_observation_context(character_id, stamp)

    def observe(
        self, character_id: UUID, now_utc: datetime | None = None
    ) -> ObservationSnapshot:
        return self.capture_context(character_id, now_utc)[1]
