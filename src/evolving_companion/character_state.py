"""Minimal persisted, explicitly updated state for one Character."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

Energy = Literal["low", "medium", "high"]
Attention = Literal["scattered", "normal", "focused"]
MoodTendency = Literal["low", "neutral", "positive"]
SocialEngagement = Literal["withdrawn", "normal", "engaged"]


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

    @field_validator("updated_at")
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

    def __init__(self, store: CharacterStateStore) -> None:
        self._store = store

    def get_state(
        self, character_id: UUID, *, initialize_at: datetime | None = None
    ) -> CharacterState:
        state = self._store.get_character_state(character_id)
        if state is None:
            state = CharacterState(
                character_id=character_id,
                updated_at=initialize_at or datetime.now(timezone.utc),
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
        values = current.model_dump()
        for name, value in (
            ("energy", energy),
            ("attention", attention),
            ("mood_tendency", mood_tendency),
            ("social_engagement", social_engagement),
        ):
            if value is not None:
                values[name] = value
        if current_activity is not UNCHANGED:
            values["current_activity"] = current_activity
        values["updated_at"] = datetime.now(timezone.utc)
        updated = CharacterState.model_validate(values)
        self._store.upsert_character_state(updated)
        return updated
