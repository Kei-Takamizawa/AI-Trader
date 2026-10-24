"""Technical indicators on daily bars in exact decimals (pure; depends only on the domain).

Every function returns ``None`` when the history is too short, so callers must decide what a
missing value means instead of getting a silently wrong number.
"""

from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise

from aitrader.domain import Bar

HUNDRED = Decimal(100)


def mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal(0)) / len(values)


def sma(values: Sequence[Decimal], period: int) -> Decimal | None:
    """Simple moving average of the last ``period`` values."""
    _check_period(period)
    if len(values) < period:
        return None
    return mean(values[-period:])


def stdev(values: Sequence[Decimal]) -> Decimal | None:
    """Sample standard deviation."""
    if len(values) < 2:
        return None
    average = sum(values, Decimal(0)) / len(values)
    variance = sum(((v - average) ** 2 for v in values), Decimal(0)) / (len(values) - 1)
    return variance.sqrt()


def returns(closes: Sequence[Decimal]) -> list[Decimal]:
    """Close-to-close simple returns (fractions)."""
    return [current / previous - 1 for previous, current in pairwise(closes)]


def true_ranges(bars: Sequence[Bar]) -> list[Decimal]:
    """True range of every bar after the first (it needs the previous close)."""
    return [
        max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
        for previous, bar in pairwise(bars)
    ]


def atr(bars: Sequence[Bar], period: int) -> Decimal | None:
    """Average true range: the simple mean of the last ``period`` true ranges."""
    _check_period(period)
    ranges = true_ranges(bars)
    if len(ranges) < period:
        return None
    return mean(ranges[-period:])


def daily_volatility(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """Standard deviation of the last ``period`` daily returns, as a fraction."""
    _check_period(period)
    values = returns(closes)
    if len(values) < period:
        return None
    return stdev(values[-period:])


def total_return(closes: Sequence[Decimal], period: int, skip: int = 0) -> Decimal | None:
    """Return over ``period`` sessions, ending ``skip`` sessions before the last close."""
    _check_period(period)
    if skip < 0:
        raise ValueError("skip must not be negative")
    if len(closes) < period + skip + 1:
        return None
    end = closes[-1 - skip]
    start = closes[-1 - skip - period]
    return end / start - 1


def rsi(closes: Sequence[Decimal], period: int) -> Decimal | None:
    """Wilder's relative strength index over the whole series, from 0 to 100."""
    _check_period(period)
    changes = [current - previous for previous, current in pairwise(closes)]
    if len(changes) < period:
        return None
    gains = [max(c, Decimal(0)) for c in changes]
    losses = [max(-c, Decimal(0)) for c in changes]
    average_gain = sum(gains[:period], Decimal(0)) / period
    average_loss = sum(losses[:period], Decimal(0)) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
    if average_loss == 0:
        return HUNDRED if average_gain > 0 else Decimal(50)
    strength = average_gain / average_loss
    return HUNDRED - HUNDRED / (1 + strength)


def _check_period(period: int) -> None:
    if period < 1:
        raise ValueError("period must be at least 1")


__all__ = [
    "atr",
    "daily_volatility",
    "mean",
    "returns",
    "rsi",
    "sma",
    "stdev",
    "total_return",
    "true_ranges",
]
