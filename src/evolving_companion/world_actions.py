"""Trusted explicit move requests; no chat parsing or autonomous execution."""

from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from evolving_companion.clock import Clock, SystemClock

ActionStatus = Literal["success", "rejected", "failed"]
ActionReason = Literal[
    "moved",
    "already_at_destination",
    "invalid_destination",
    "location_precondition_failed",
    "character_id_mismatch",
    "life_context_missing",
    "clock_moved_backwards",
    "action_id_conflict",
    "storage_error",
    "commit_outcome_unknown",
]


class MoveToIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID
    character_id: UUID
    action_type: Literal["move_to"] = "move_to"
    destination_entity_id: UUID
    # 必填；None 明确表示调用方依据的当前位置未知，不是省略前置条件。
    expected_location_entity_id: UUID | None


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: UUID
    character_id: UUID
    status: ActionStatus
    reason: ActionReason
    destination_entity_id: UUID
    location_before: UUID | None = None
    location_after: UUID | None = None
    resolved_at: datetime
    replayed: bool = False
    outcome_known: bool = True

    @field_validator("resolved_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("resolved_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class WorldActionStore(Protocol):
    def resolve_world_action(
        self, intent: MoveToIntent, character_id: UUID, resolved_at: datetime
    ) -> ActionResult: ...

    def get_world_action_result(self, action_id: UUID) -> ActionResult | None: ...


class ActionResolver:
    """A bound Character resolver, called only by trusted developer/test code."""

    def __init__(
        self, store: WorldActionStore, character_id: UUID, clock: Clock | None = None
    ) -> None:
        if not isinstance(character_id, UUID):
            raise TypeError("character_id must be identity.internal_id (UUID)")
        self._store = store
        self._character_id = character_id
        self._clock = clock or SystemClock()

    def resolve(self, intent: MoveToIntent) -> ActionResult:
        if not isinstance(intent, MoveToIntent):
            raise TypeError("resolve requires a typed MoveToIntent")
        return self._store.resolve_world_action(
            intent, self._character_id, self._clock.now_utc()
        )

    def get_result(self, action_id: UUID) -> ActionResult | None:
        if not isinstance(action_id, UUID):
            raise TypeError("action_id must be a UUID")
        return self._store.get_world_action_result(action_id)
