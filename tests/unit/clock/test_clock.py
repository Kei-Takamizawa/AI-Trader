from datetime import UTC, datetime, timedelta

import pytest
import time_machine

from aitrader.clock import Clock, ClockError, SimClock, WallClock

T0 = datetime(2026, 10, 23, 13, 30, tzinfo=UTC)


def test_sim_clock_moves_forward_only() -> None:
    clock = SimClock(T0)
    assert clock.now() == T0
    clock.advance(timedelta(minutes=5))
    assert clock.now() == T0 + timedelta(minutes=5)
    clock.set(T0 + timedelta(hours=1))
    assert clock.now() == T0 + timedelta(hours=1)
    with pytest.raises(ClockError, match="backwards"):
        clock.set(T0)
    with pytest.raises(ClockError, match="negative"):
        clock.advance(timedelta(seconds=-1))


def test_sim_clock_requires_aware_datetimes() -> None:
    with pytest.raises(ValueError, match="naive"):
        SimClock(datetime(2026, 1, 1))  # noqa: DTZ001
    clock = SimClock(T0)
    with pytest.raises(ValueError, match="naive"):
        clock.set(datetime(2027, 1, 1))  # noqa: DTZ001


def test_wall_clock_returns_utc() -> None:
    with time_machine.travel(T0, tick=False):
        now = WallClock().now()
    assert now == T0
    assert now.tzinfo is UTC


def test_both_clocks_satisfy_the_protocol() -> None:
    assert isinstance(WallClock(), Clock)
    assert isinstance(SimClock(T0), Clock)
