"""Character-local time projection and last-interaction semantics."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from evolving_companion.clock import Clock, SystemClock


class CharacterRuntimeStore(Protocol):
    def get_last_interaction_at(self, character_id: UUID) -> datetime | None: ...

    def record_last_interaction(self, character_id: UUID, at: datetime) -> datetime: ...


@dataclass(frozen=True)
class CharacterTimeSnapshot:
    now_utc: datetime
    now_local: datetime
    timezone: str
    local_date: date
    local_time: time
    last_interaction_at: datetime | None
    offline_duration: timedelta | None
    diagnostics: tuple[str, ...] = ()


class CharacterTimeService:
    def __init__(
        self,
        store: CharacterRuntimeStore,
        character_id: UUID,
        timezone_name: str,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._character_id = character_id
        self._timezone_name = timezone_name
        self._zone = ZoneInfo(timezone_name)
        self._clock = clock or SystemClock()

    def snapshot(self, now_utc: datetime | None = None) -> CharacterTimeSnapshot:
        now = _utc(now_utc or self._clock.now_utc())
        last = self._store.get_last_interaction_at(self._character_id)
        diagnostics: tuple[str, ...] = ()
        offline = None if last is None else now - last
        if offline is not None and offline < timedelta(0):
            offline = timedelta(0)
            diagnostics = ("clock_moved_backwards",)
        local = now.astimezone(self._zone)
        return CharacterTimeSnapshot(
            now,
            local,
            self._timezone_name,
            local.date(),
            local.timetz(),
            last,
            offline,
            diagnostics,
        )

    def record_successful_interaction(self, at: datetime | None = None) -> datetime:
        return self._store.record_last_interaction(
            self._character_id, _utc(at or self._clock.now_utc())
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must be timezone-aware")
    return value.astimezone(timezone.utc)


def format_offline_duration(duration: timedelta) -> str:
    seconds = max(0, int(duration.total_seconds()))
    if seconds < 60:
        return "不到 1 分钟"
    if seconds < 3600:
        return f"约 {max(1, seconds // 60)} 分钟"
    if seconds < 86400:
        return f"约 {max(1, seconds // 3600)} 小时"
    return f"约 {max(1, seconds // 86400)} 天"
