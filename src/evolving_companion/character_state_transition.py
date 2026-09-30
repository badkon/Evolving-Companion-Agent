"""Small deterministic event and elapsed-time transitions for Character State."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from evolving_companion.clock import Clock, SystemClock

from evolving_companion.character_state import (
    CharacterState,
    CharacterStateService,
    Energy,
    MoodTendency,
    SocialEngagement,
    TEMPORAL_ANCHORS,
)

StateEventType = Literal[
    "conversation_started",
    "conversation_ended",
    "conversation_turn_completed",
    "focused_task_started",
    "focused_task_ended",
    "rest_started",
    "rest_ended",
    "activity_set",
    "activity_cleared",
    "time_elapsed",
    "mood_up",
    "mood_down",
    "mood_reset",
    "social_engagement_up",
    "social_engagement_down",
]
_EVENT_TYPES = frozenset(
    {
        "conversation_started",
        "conversation_ended",
        "conversation_turn_completed",
        "focused_task_started",
        "focused_task_ended",
        "rest_started",
        "rest_ended",
        "activity_set",
        "activity_cleared",
        "time_elapsed",
        "mood_up",
        "mood_down",
        "mood_reset",
        "social_engagement_up",
        "social_engagement_down",
    }
)


def _as_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CharacterStateEvent:
    """Transient, explicit event; events are not written to an event log."""

    event_type: StateEventType
    occurred_at: datetime | None = None
    activity_name: str | None = None

    def __post_init__(self) -> None:
        if self.event_type not in _EVENT_TYPES:
            raise ValueError(f"Unsupported State event: {self.event_type}")
        if self.occurred_at is not None:
            object.__setattr__(
                self, "occurred_at", _as_utc(self.occurred_at, "occurred_at")
            )
        if self.activity_name is not None and not self.activity_name.strip():
            raise ValueError("activity_name must not be empty")


@dataclass(frozen=True)
class CharacterStateTransitionResult:
    before_state: CharacterState
    after_state: CharacterState
    changed_fields: tuple[str, ...]
    event_type: StateEventType
    reason: str
    diagnostics: tuple[str, ...] = ()


class CharacterStateTransitionService:
    """Apply bounded deterministic rules and persist only the current snapshot."""

    _STATE_FIELDS = (
        "energy",
        "attention",
        "mood_tendency",
        "social_engagement",
        "current_activity",
    )

    def __init__(
        self, state_service: CharacterStateService, clock: Clock | None = None
    ) -> None:
        self._state_service = state_service
        self._clock = clock or SystemClock()

    def apply_event(
        self, character_id: UUID, event: CharacterStateEvent
    ) -> CharacterStateTransitionResult:
        occurred_at = _as_utc(event.occurred_at or self._clock.now_utc(), "occurred_at")
        if event.event_type == "time_elapsed":
            return self.apply_elapsed_time(character_id, occurred_at)
        before = self._state_service.get_state(character_id, initialize_at=occurred_at)
        if occurred_at < before.updated_at:
            return self._result(
                before,
                before,
                event.event_type,
                "事件时间早于状态更新时间；State 字段和时间锚点均保持不变。",
                ("clock_moved_backwards",),
            )

        changes: dict[str, str | None] = {}
        reason = "该事件不改变当前 State。"
        if event.event_type == "rest_started":
            changes["energy"] = self._step_energy_up(before.energy)
            reason = "休息开始使精力向较高档位移动一级。"
        elif event.event_type == "focused_task_started":
            changes["attention"] = "focused"
            if event.activity_name is not None:
                changes["current_activity"] = event.activity_name
            reason = "专注任务开始，将注意力设为集中；未因开始任务扣减精力。"
        elif event.event_type == "focused_task_ended":
            changes["attention"] = "normal"
            reason = "专注任务结束，将注意力恢复为平常；不自动清除活动。"
        elif event.event_type == "activity_set":
            if event.activity_name is None:
                raise ValueError("activity_set requires activity_name")
            changes["current_activity"] = event.activity_name
            reason = "按显式事件设置当前活动。"
        elif event.event_type == "activity_cleared":
            changes["current_activity"] = None
            reason = "按显式事件清除当前活动。"
        elif event.event_type == "mood_up":
            changes["mood_tendency"] = self._step_mood_up(before.mood_tendency)
            reason = "显式 mood_up 使心境倾向向上移动一级。"
        elif event.event_type == "mood_down":
            changes["mood_tendency"] = self._step_mood_down(before.mood_tendency)
            reason = "显式 mood_down 使心境倾向向下移动一级。"
        elif event.event_type == "mood_reset":
            changes["mood_tendency"] = "neutral"
            reason = "显式 mood_reset 将心境倾向设为 neutral。"
        elif event.event_type == "social_engagement_up":
            changes["social_engagement"] = self._step_social_up(
                before.social_engagement
            )
            reason = "显式 social_engagement_up 使社交投入向上移动一级。"
        elif event.event_type == "social_engagement_down":
            changes["social_engagement"] = self._step_social_down(
                before.social_engagement
            )
            reason = "显式 social_engagement_down 使社交投入向下移动一级。"

        values = before.model_dump()
        values.update(changes)
        if not any(
            getattr(before, field) != values[field] for field in self._STATE_FIELDS
        ):
            return self._result(before, before, event.event_type, reason)
        values["updated_at"] = occurred_at
        for name, anchor in TEMPORAL_ANCHORS.items():
            if values[name] != getattr(before, name):
                values[anchor] = occurred_at
        after = CharacterState.model_validate(values)
        self._state_service.save_state(after)
        return self._result(before, after, event.event_type, reason)

    def apply_elapsed_time(
        self, character_id: UUID, now: datetime | None = None
    ) -> CharacterStateTransitionResult:
        now = _as_utc(now or self._clock.now_utc(), "now")
        before = self._state_service.get_state(character_id, initialize_at=now)
        if now < before.updated_at:
            return self._result(
                before,
                before,
                "time_elapsed",
                "当前时间早于状态更新时间；State 字段和时间锚点均保持不变。",
                ("clock_moved_backwards",),
            )
        values = before.model_dump()
        energy_elapsed = now - before.energy_updated_at
        if (before.energy == "low" and energy_elapsed >= timedelta(hours=3)) or (
            before.energy == "high" and energy_elapsed >= timedelta(hours=8)
        ):
            values["energy"] = "medium"
        if now - before.attention_updated_at >= timedelta(hours=1):
            values["attention"] = "normal"
        if now - before.mood_updated_at >= timedelta(hours=6):
            values["mood_tendency"] = "neutral"
        if now - before.social_updated_at >= timedelta(hours=6):
            values["social_engagement"] = "normal"

        changed = tuple(
            name for name in self._STATE_FIELDS if values[name] != getattr(before, name)
        )
        if not changed:
            return self._result(
                before,
                before,
                "time_elapsed",
                "时间检查未产生 State 变化；保留全部时间锚点。",
            )
        for name in changed:
            values[TEMPORAL_ANCHORS[name]] = now
        values["updated_at"] = now
        after = CharacterState.model_validate(values)
        self._state_service.save_state(after)
        return self._result(
            before,
            after,
            "time_elapsed",
            "按字段独立 elapsed 将短期状态归一到工程 baseline："
            + "、".join(changed)
            + "。",
        )

    @staticmethod
    def _step_energy_up(energy: Energy) -> Energy:
        levels: dict[Energy, Energy] = {
            "low": "medium",
            "medium": "high",
            "high": "high",
        }
        return levels[energy]

    @staticmethod
    def _step_mood_up(mood: MoodTendency) -> MoodTendency:
        levels: dict[MoodTendency, MoodTendency] = {
            "low": "neutral",
            "neutral": "positive",
            "positive": "positive",
        }
        return levels[mood]

    @staticmethod
    def _step_mood_down(mood: MoodTendency) -> MoodTendency:
        levels: dict[MoodTendency, MoodTendency] = {
            "positive": "neutral",
            "neutral": "low",
            "low": "low",
        }
        return levels[mood]

    @staticmethod
    def _step_social_up(social: SocialEngagement) -> SocialEngagement:
        levels: dict[SocialEngagement, SocialEngagement] = {
            "withdrawn": "normal",
            "normal": "engaged",
            "engaged": "engaged",
        }
        return levels[social]

    @staticmethod
    def _step_social_down(social: SocialEngagement) -> SocialEngagement:
        levels: dict[SocialEngagement, SocialEngagement] = {
            "engaged": "normal",
            "normal": "withdrawn",
            "withdrawn": "withdrawn",
        }
        return levels[social]

    @classmethod
    def _result(
        cls,
        before: CharacterState,
        after: CharacterState,
        event_type: StateEventType,
        reason: str,
        diagnostics: tuple[str, ...] = (),
    ) -> CharacterStateTransitionResult:
        changed_fields = tuple(
            field
            for field in cls._STATE_FIELDS
            if getattr(before, field) != getattr(after, field)
        )
        return CharacterStateTransitionResult(
            before,
            after,
            changed_fields,
            event_type,
            reason,
            diagnostics,
        )
