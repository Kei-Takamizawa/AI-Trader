"""Clocks. Code outside this package must never read the system time directly."""

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from aitrader.domain.types import require_utc


class ClockError(Exception):
    """Invalid clock operation, such as moving a simulated clock backwards."""


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""
        ...


class WallClock:
    """The real time (PAPER and LIVE)."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SimClock:
    """A settable clock for backtests and tests. It never moves backwards."""

    def __init__(self, start: datetime) -> None:
        self._now = require_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, ts: datetime) -> None:
        target = require_utc(ts)
        if target < self._now:
            raise ClockError(f"cannot move the clock backwards from {self._now} to {target}")
        self._now = target

    def advance(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise ClockError("cannot advance by a negative duration")
        self._now = self._now + delta
