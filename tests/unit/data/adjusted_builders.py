"""Synthetic vendor snapshots; no downloaded market data."""

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from aitrader.clock import TradingCalendar
from aitrader.data import DailyBarRow, DailyBarSource, write_daily_csv

D = Decimal
CAL = TradingCalendar()


def sources(
    root: Path,
    sessions: Sequence[date],
    closes: Sequence[Decimal],
    factors: Mapping[date, Decimal],
    *,
    volume: Decimal = Decimal(1_000_001),
) -> tuple[DailyBarSource, DailyBarSource]:
    raw, adjusted = [], []
    for session, close in zip(sessions, closes, strict=True):
        multiplier = D(1)
        for ex_date, factor in factors.items():
            if session < ex_date:
                multiplier *= factor
        row = DailyBarRow(
            session=session,
            open=close,
            high=close + D("0.2"),
            low=close - D("0.2"),
            close=close,
            volume=volume,
        )
        raw.append(row)
        adjusted.append(
            DailyBarRow(
                session=session,
                open=(row.open * multiplier).quantize(D("0.0001"), rounding=ROUND_HALF_EVEN),
                high=(row.high * multiplier).quantize(D("0.0001"), rounding=ROUND_HALF_EVEN),
                low=(row.low * multiplier).quantize(D("0.0001"), rounding=ROUND_HALF_EVEN),
                close=(row.close * multiplier).quantize(D("0.0001"), rounding=ROUND_HALF_EVEN),
                volume=volume,
            )
        )
    write_daily_csv(root / "raw", "SPY", raw)
    write_daily_csv(root / "adjusted", "SPY", adjusted)
    return DailyBarSource(root / "raw", CAL), DailyBarSource(root / "adjusted", CAL)
