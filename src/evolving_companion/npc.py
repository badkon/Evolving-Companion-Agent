"""Tier-0 World records, not Characters or independently running agents."""

from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from evolving_companion.clock import Clock, SystemClock


class NPCRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    npc_id: UUID
    canonical_name: str
    display_name: str | None = None
    # World 当前记录的位置，不代表玲观察到、知道或记得这个 NPC。
    place_entity_id: UUID | None = None
    active: bool = True
    tags: tuple[str, ...] = ()
    short_description: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("canonical_name", "display_name", "short_description")
    @classmethod
    def require_nonempty_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("NPC text must not be empty; use None to clear")
        return value

    @field_validator("tags")
    @classmethod
    def require_nonempty_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not tag.strip() for tag in value):
            raise ValueError("NPC tags must not be empty")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("NPC timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class NPCStore(Protocol):
    def get_npc(self, npc_id: UUID) -> NPCRecord | None: ...
    def list_active_npcs(self) -> tuple[NPCRecord, ...]: ...
    def insert_npc(self, npc: NPCRecord) -> None: ...
    def patch_npc(
        self, npc_id: UUID, changes: dict[str, object], now: datetime
    ) -> NPCRecord: ...


class _Unchanged(Enum):
    token = "unchanged"


UNCHANGED = _Unchanged.token


class NPCService:
    """Explicit record access only; construction does not initialize NPCs."""

    def __init__(self, store: NPCStore, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    def get(self, npc_id: UUID) -> NPCRecord | None:
        return self._store.get_npc(npc_id)

    def list_active(self) -> tuple[NPCRecord, ...]:
        return self._store.list_active_npcs()

    def create(
        self,
        canonical_name: str,
        *,
        display_name: str | None = None,
        place_entity_id: UUID | None = None,
        active: bool = True,
        tags: tuple[str, ...] = (),
        short_description: str | None = None,
    ) -> NPCRecord:
        now = self._clock.now_utc()
        npc = NPCRecord(
            npc_id=uuid4(),
            canonical_name=canonical_name,
            display_name=display_name,
            place_entity_id=place_entity_id,
            active=active,
            tags=tags,
            short_description=short_description,
            created_at=now,
            updated_at=now,
        )
        self._store.insert_npc(npc)
        return npc

    def update(
        self,
        npc_id: UUID,
        *,
        canonical_name: str | _Unchanged = UNCHANGED,
        display_name: str | None | _Unchanged = UNCHANGED,
        place_entity_id: UUID | None | _Unchanged = UNCHANGED,
        active: bool | _Unchanged = UNCHANGED,
        tags: tuple[str, ...] | _Unchanged = UNCHANGED,
        short_description: str | None | _Unchanged = UNCHANGED,
    ) -> NPCRecord:
        values: dict[str, object] = {}
        for name, value in (
            ("canonical_name", canonical_name),
            ("display_name", display_name),
            ("place_entity_id", place_entity_id),
            ("active", active),
            ("tags", tags),
            ("short_description", short_description),
        ):
            if value is not UNCHANGED:
                values[name] = value
        return self._store.patch_npc(npc_id, values, self._clock.now_utc())

    def set_location(self, npc_id: UUID, place_entity_id: UUID | None) -> NPCRecord:
        return self.update(npc_id, place_entity_id=place_entity_id)

    def set_active(self, npc_id: UUID, active: bool) -> NPCRecord:
        return self.update(npc_id, active=active)
