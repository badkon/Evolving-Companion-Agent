"""Minimal persisted, explicitly updated state for one Character."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from evolving_companion.clock import Clock, SystemClock

Energy = Literal["low", "medium", "high"]
Attention = Literal["scattered", "normal", "focused"]
MoodTendency = Literal["low", "neutral", "positive"]
SocialEngagement = Literal["withdrawn", "normal", "engaged"]

TEMPORAL_ANCHORS = {
    "energy": "energy_updated_at",
    "attention": "attention_updated_at",
    "mood_tendency": "mood_updated_at",
    "social_engagement": "social_updated_at",
}


class CharacterState(BaseModel):
    """Small current-state snapshot; it is not personality or long-term memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # SQLite stores the canonical UUID string from identity.internal_id.
    character_id: UUID
    energy: Energy = "medium"
    attention: Attention = "normal"
    mood_tendency: MoodTendency = "neutral"
    social_engagement: SocialEngagement = "normal"
    current_activity: str | None = None
    updated_at: datetime
    # 各短期状态独立累计时间；一次精力变化不会重置心境等字段的时限。
    energy_updated_at: datetime
    attention_updated_at: datetime
    mood_updated_at: datetime
    social_updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def initialize_temporal_anchors(cls, value: Any) -> Any:
        if isinstance(value, dict) and "updated_at" in value:
            value = dict(value)
            for anchor in TEMPORAL_ANCHORS.values():
                value.setdefault(anchor, value["updated_at"])
        return value

    @field_validator("updated_at", *TEMPORAL_ANCHORS.values())
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware UTC")
        return value.astimezone(timezone.utc)


class _Unchanged(Enum):
    token = "unchanged"


# 显式区分“不更新活动”与“清除当前活动（None）”。
UNCHANGED = _Unchanged.token


class CharacterStateStore(Protocol):
    def get_character_state(self, character_id: UUID) -> CharacterState | None: ...

    def upsert_character_state(self, state: CharacterState) -> None: ...


class CharacterStateService:
    """Initialize state once and apply explicit partial updates."""

    def __init__(self, store: CharacterStateStore, clock: Clock | None = None) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    def get_state(
        self, character_id: UUID, *, initialize_at: datetime | None = None
    ) -> CharacterState:
        state = self._store.get_character_state(character_id)
        if state is None:
            state = CharacterState.model_validate(
                {
                    "character_id": character_id,
                    "updated_at": initialize_at or self._clock.now_utc(),
                }
            )
            self._store.upsert_character_state(state)
        return state

    def save_state(self, state: CharacterState) -> None:
        """Persist a complete explicitly computed state snapshot."""
        self._store.upsert_character_state(state)

    def update_state(
        self,
        character_id: UUID,
        *,
        energy: Energy | None = None,
        attention: Attention | None = None,
        mood_tendency: MoodTendency | None = None,
        social_engagement: SocialEngagement | None = None,
        current_activity: str | None | _Unchanged = UNCHANGED,
    ) -> CharacterState:
        current = self.get_state(character_id)
        now = self._clock.now_utc()
        if now < current.updated_at:
            return current
        values = current.model_dump()
        for name, value in (
            ("energy", energy),
            ("attention", attention),
            ("mood_tendency", mood_tendency),
            ("social_engagement", social_engagement),
        ):
            if value is not None and value != getattr(current, name):
                values[name] = value
                values[TEMPORAL_ANCHORS[name]] = now
        if current_activity is not UNCHANGED:
            values["current_activity"] = current_activity
        if values == current.model_dump():
            return current
        values["updated_at"] = now
        updated = CharacterState.model_validate(values)
        self._store.upsert_character_state(updated)
        return updated
