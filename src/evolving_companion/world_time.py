"""Lazy, derived time context; never a simulation or persistent world state."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from evolving_companion.clock import Clock, SystemClock

DayPeriod = Literal["morning", "afternoon", "evening", "night"]


@dataclass(frozen=True)
class WorldTimeSnapshot:
    now_utc: datetime
    now_local: datetime
    timezone_name: str
    day_period: DayPeriod


class WorldTimeService:
    def __init__(self, timezone_name: str, clock: Clock | None = None) -> None:
        self._timezone_name = timezone_name
        self._zone = ZoneInfo(timezone_name)
        self._clock = clock or SystemClock()

    def snapshot(self, now_utc: datetime | None = None) -> WorldTimeSnapshot:
        now = now_utc if now_utc is not None else self._clock.now_utc()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now_utc must be timezone-aware")
        now = now.astimezone(timezone.utc)
        local = now.astimezone(self._zone)
        # 固定工程分段，不代表日出日落、作息或任何实际活动。
        period: DayPeriod
        if 6 <= local.hour < 12:
            period = "morning"
        elif 12 <= local.hour < 18:
            period = "afternoon"
        elif 18 <= local.hour < 22:
            period = "evening"
        else:
            period = "night"
        return WorldTimeSnapshot(now, local, self._timezone_name, period)
