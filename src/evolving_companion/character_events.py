"""Transient, explicitly created Character Events and their State handler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from evolving_companion.clock import Clock, SystemClock

from evolving_companion.character_state import CharacterState, CharacterStateService
from evolving_companion.character_state_transition import (
    CharacterStateEvent as StateTransitionEvent,
    CharacterStateTransitionResult,
    CharacterStateTransitionService,
    StateEventType,
)

CharacterEventType = Literal[
    "conversation_session_started",
    "conversation_session_ended",
    "activity_started",
    "activity_ended",
    "rest_started",
    "rest_ended",
    "focused_task_started",
    "focused_task_ended",
]
CharacterEventSource = Literal["developer", "system", "conversation", "activity"]
MAX_FUTURE_SKEW = timedelta(minutes=5)


class ActivityEventPayload(BaseModel):
    """The sole structured payload currently supported by Character Events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    activity_name: str = Field(min_length=1)

    @field_validator("activity_name")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("activity_name must not be empty")
        return value


class CharacterEvent(BaseModel):
    """A validated event fact, distinct from State, Memory, and chat messages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID = Field(default_factory=uuid4)
    character_id: UUID
    event_type: CharacterEventType
    occurred_at: datetime | None = None
    payload: ActivityEventPayload | None = None
    source: CharacterEventSource

    @field_validator("occurred_at")
    @classmethod
    def validate_event_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        utc_value = value.astimezone(timezone.utc)
        return utc_value

    @model_validator(mode="after")
    def validate_payload_for_event(self) -> CharacterEvent:
        if self.event_type == "activity_started" and self.payload is None:
            raise ValueError("activity_started requires activity_name payload")
        if (
            self.event_type
            not in {
                "activity_started",
                "activity_ended",
                "focused_task_started",
                "focused_task_ended",
            }
            and self.payload is not None
        ):
            raise ValueError(f"{self.event_type} does not accept a payload")
        return self


@dataclass(frozen=True)
class EventHandlingResult:
    event_id: UUID
    event_type: CharacterEventType
    character_id: UUID
    transition_result: CharacterStateTransitionResult | None
    handled: bool
    reason: str
    before_state: CharacterState | None
    after_state: CharacterState | None
    diagnostics: tuple[str, ...] = ()


class CharacterEventService:
    """Map explicit Character Events to the existing State transition rules."""

    def __init__(
        self,
        character_id: UUID,
        state_service: CharacterStateService,
        transition_service: CharacterStateTransitionService,
        clock: Clock | None = None,
    ) -> None:
        self._character_id = character_id
        self._state_service = state_service
        self._transition_service = transition_service
        self._clock = clock or SystemClock()

    def create_event(self, **values: object) -> CharacterEvent:
        values.setdefault("occurred_at", self._clock.now_utc())
        return CharacterEvent.model_validate(values)

    def handle(self, event: CharacterEvent) -> EventHandlingResult:
        occurred_at = event.occurred_at or self._clock.now_utc()
        if occurred_at > self._clock.now_utc() + MAX_FUTURE_SKEW:
            return self._result(
                event,
                None,
                False,
                "事件时间异常地位于未来。",
                diagnostics=("event_time_too_far_in_future",),
            )
        if event.occurred_at is None:
            event = event.model_copy(update={"occurred_at": occurred_at})
        if event.character_id != self._character_id:
            return self._result(
                event,
                None,
                False,
                "event character_id does not match the configured identity.internal_id.",
                diagnostics=("character_id_mismatch",),
            )

        before = self._state_service.get_state(
            event.character_id, initialize_at=event.occurred_at
        )
        try:
            transition_type, activity_name = self._map_event(event, before)
            if transition_type is None:
                return self._result(
                    event,
                    None,
                    False,
                    self._no_transition_reason(event),
                    before_state=before,
                    after_state=before,
                    diagnostics=("event_not_applied",),
                )

            transition = self._transition_service.apply_event(
                event.character_id,
                StateTransitionEvent(
                    transition_type,
                    occurred_at=event.occurred_at,
                    activity_name=activity_name,
                ),
            )
            diagnostic = transition.diagnostics
            handled = not diagnostic
            reason = "事件已处理。" if handled else "事件未应用：" + transition.reason
            return self._result(
                event,
                transition,
                handled,
                reason,
                before_state=transition.before_state,
                after_state=transition.after_state,
                diagnostics=diagnostic,
            )
        except Exception as error:
            # State transitions persist atomically; report the failure without
            # writing an event or creating a partial state update here.
            try:
                after = self._state_service.get_state(event.character_id)
            except Exception:
                after = None
            return self._result(
                event,
                None,
                False,
                f"事件处理失败：{type(error).__name__}",
                before_state=before,
                after_state=after,
                diagnostics=("transition_failed",),
            )

    def _map_event(
        self, event: CharacterEvent, current: CharacterState
    ) -> tuple[StateEventType | None, str | None]:
        payload_name = event.payload.activity_name if event.payload else None
        if event.event_type == "conversation_session_started":
            return "conversation_started", None
        if event.event_type == "conversation_session_ended":
            return "conversation_ended", None
        if event.event_type == "activity_started":
            return "activity_set", payload_name
        if event.event_type == "activity_ended":
            if payload_name is None:
                return None, None
            if payload_name != current.current_activity:
                return None, None
            return "activity_cleared", None
        if event.event_type == "rest_started":
            return "rest_started", None
        if event.event_type == "rest_ended":
            return "rest_ended", None
        if event.event_type == "focused_task_started":
            return "focused_task_started", payload_name
        if event.event_type == "focused_task_ended":
            return "focused_task_ended", None
        raise ValueError(f"Unsupported Character Event: {event.event_type}")

    @staticmethod
    def _no_transition_reason(event: CharacterEvent) -> str:
        if event.event_type == "activity_ended":
            if event.payload is None:
                return "activity_ended 缺少 activity_name，未清除当前活动。"
            return "结束的活动与当前活动不匹配，未清除当前活动。"
        return "该事件当前不映射到 State 变化。"

    @staticmethod
    def _result(
        event: CharacterEvent,
        transition: CharacterStateTransitionResult | None,
        handled: bool,
        reason: str,
        *,
        before_state: CharacterState | None = None,
        after_state: CharacterState | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> EventHandlingResult:
        return EventHandlingResult(
            event_id=event.event_id,
            event_type=event.event_type,
            character_id=event.character_id,
            transition_result=transition,
            handled=handled,
            reason=reason,
            before_state=before_state,
            after_state=after_state,
            diagnostics=diagnostics,
        )
