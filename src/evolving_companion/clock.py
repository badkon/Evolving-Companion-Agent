"""Small injectable UTC clock abstraction."""

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Deterministic clock for tests and offline smoke scripts."""

    def __init__(self, current: datetime) -> None:
        self.set(current)

    def now_utc(self) -> datetime:
        return self._current

    def set(self, current: datetime) -> None:
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("clock time must be timezone-aware")
        self._current = current.astimezone(timezone.utc)

    def advance(self, delta: timedelta | None = None, **parts: float) -> None:
        self._current += delta if delta is not None else timedelta(**parts)
