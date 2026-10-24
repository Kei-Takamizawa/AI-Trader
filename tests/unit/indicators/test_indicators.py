"""Indicators against hand-computed values."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aitrader.domain import Bar
from aitrader.indicators import (
    atr,
    daily_volatility,
    mean,
    returns,
    rsi,
    sma,
    stdev,
    total_return,
    true_ranges,
)

D = Decimal
T0 = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)


def bar(day: int, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        symbol="SPY",
        ts=T0 + timedelta(days=day),
        open=D(o),
        high=D(h),
        low=D(low),
        close=D(c),
        volume=D(1000),
    )


def test_mean_sma_and_stdev() -> None:
    values = [D(1), D(2), D(3), D(4)]
    assert mean(values) == D("2.5")
    assert mean([]) is None
    assert sma(values, 2) == D("3.5")
    assert sma(values, 5) is None
    assert stdev([D(2), D(4), D(4), D(4), D(5), D(5), D(7), D(9)]) == (D(32) / D(7)).sqrt()
    assert stdev([D(1)]) is None
    assert stdev([D(1), D(3)]) == D(2).sqrt()


def test_returns_and_total_return() -> None:
    closes = [D(100), D(110), D(99)]
    assert returns(closes) == [D("0.1"), D(99) / D(110) - 1]
    assert total_return(closes, 2) == D("-0.01")
    assert total_return(closes, 1, skip=1) == D("0.1")
    assert total_return(closes, 3) is None
    with pytest.raises(ValueError, match="skip"):
        total_return(closes, 1, skip=-1)


def test_true_range_uses_the_previous_close() -> None:
    bars = [
        bar(0, "10", "11", "9", "10"),
        bar(1, "12", "13", "11.5", "12.5"),
        bar(2, "12", "12.2", "8", "9"),
    ]
    assert true_ranges(bars) == [D(3), D("4.5")]  # gap up from 10: 13 - 10; then 12.5 - 8
    assert atr(bars, 2) == D("3.75")
    assert atr(bars, 3) is None


def test_daily_volatility() -> None:
    closes = [D(100), D(101), D(100), D(101)]
    vol = daily_volatility(closes, 3)
    assert vol is not None
    assert vol > 0
    assert daily_volatility(closes, 4) is None


def test_rsi_extremes_and_wilder_smoothing() -> None:
    assert rsi([D(1), D(2), D(3)], 2) == 100
    assert rsi([D(3), D(2), D(1)], 2) == 0
    assert rsi([D(1), D(1), D(1)], 2) == 50
    assert rsi([D(1), D(2)], 2) is None
    # changes +1, -1, then -2: gain 0.5 -> 0.25, loss 0.5 -> 1.25; RSI = 100 - 100 / (1 + 0.2)
    value = rsi([D(10), D(11), D(10), D(8)], 2)
    assert value == D(100) - D(100) / (1 + D("0.25") / D("1.25"))


@pytest.mark.parametrize("function", [sma, daily_volatility])
def test_period_must_be_positive(function: object) -> None:
    with pytest.raises(ValueError, match="period"):
        function([D(1), D(2)], 0)  # type: ignore[operator]
