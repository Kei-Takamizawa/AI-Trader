"""Download the universe, store raw and adjusted series, and check what came back.

``raw/`` holds the prices that were actually traded (for fills); ``adjusted/`` holds prices
adjusted for splits and distributions (``adjustment=all``, for signals). The report lists
missing sessions (DQ-2), large daily moves (DQ-3) and corporate actions found by comparing the
two series (DQ-5).
"""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from aitrader.clock import TradingCalendar
from aitrader.data.adjusted import (
    RATIO_TOLERANCE as RATIO_TOLERANCE,
)
from aitrader.data.adjusted import (
    SPLIT_THRESHOLD as SPLIT_THRESHOLD,
)
from aitrader.data.adjusted import (
    detect_corporate_actions,
)
from aitrader.data.alpaca import AlpacaDataClient, DailyBarRow
from aitrader.data.bars import DailyBarSource

LARGE_MOVE = Decimal("0.25")


def write_daily_csv(directory: Path, symbol: str, rows: Sequence[DailyBarRow]) -> Path:
    """Write ``<symbol>.csv`` atomically in the format ``DailyBarSource`` reads."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["date,open,high,low,close,volume"]
    lines += [
        f"{r.session.isoformat()},{r.open},{r.high},{r.low},{r.close},{r.volume}" for r in rows
    ]
    target = directory / f"{symbol}.csv"
    temporary = directory / f".{symbol}.csv.tmp"
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def missing_sessions(
    rows: Sequence[DailyBarRow], end: date, calendar: TradingCalendar
) -> list[date]:
    """Sessions between the first bar and ``end`` that have no bar."""
    if not rows:
        return []
    present = {r.session for r in rows}
    return [s for s in calendar.sessions(rows[0].session, end) if s not in present]


def large_moves(
    rows: Sequence[DailyBarRow], threshold: Decimal = LARGE_MOVE
) -> list[tuple[date, Decimal]]:
    return [
        (current.session, current.close / previous.close - 1)
        for previous, current in pairwise(rows)
        if abs(current.close / previous.close - 1) > threshold
    ]


def corporate_actions(
    raw: Sequence[DailyBarRow], adjusted: Sequence[DailyBarRow]
) -> list[tuple[date, str, Decimal]]:
    """Sessions where the raw/adjusted price ratio changes: (session, kind, factor)."""
    return [
        (event.session, event.kind.value, event.factor)
        for event in detect_corporate_actions(
            "", [(r.session, r.close) for r in raw], [(r.session, r.close) for r in adjusted]
        )
    ]


@dataclass(frozen=True)
class SymbolReport:
    rows_raw: int
    rows_adjusted: int
    first: date | None
    last: date | None
    missing_sessions: tuple[date, ...]
    large_moves: tuple[tuple[date, Decimal], ...]
    corporate_actions: tuple[tuple[date, str, Decimal], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "rows_raw": self.rows_raw,
            "rows_adjusted": self.rows_adjusted,
            "first": self.first.isoformat() if self.first else None,
            "last": self.last.isoformat() if self.last else None,
            "missing_sessions_dq2": [d.isoformat() for d in self.missing_sessions],
            "large_moves_dq3": [
                {"session": d.isoformat(), "move": str(m)} for d, m in self.large_moves
            ],
            "corporate_actions_dq5": [
                {"session": d.isoformat(), "kind": kind, "factor": str(factor)}
                for d, kind, factor in self.corporate_actions
            ],
        }


@dataclass(frozen=True)
class FetchReport:
    start: date
    end: date
    snapshot_hashes: dict[str, str]
    symbols: dict[str, SymbolReport]

    def as_dict(self) -> dict[str, object]:
        return {
            "source": "alpaca",
            "feed": "sip",
            "period": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "snapshot_hashes": dict(sorted(self.snapshot_hashes.items())),
            "symbols": {s: r.as_dict() for s, r in sorted(self.symbols.items())},
        }


def fetch_universe(
    client: AlpacaDataClient,
    symbols: Sequence[str],
    start: date,
    end: date,
    out_dir: Path,
    calendar: TradingCalendar,
) -> FetchReport:
    """Download, store, check and report; the report is also written to ``fetch_report.json``."""
    raw = client.daily_bars(symbols, start, end, adjustment="raw")
    adjusted = client.daily_bars(symbols, start, end, adjustment="all")
    reports: dict[str, SymbolReport] = {}
    for symbol in symbols:
        raw_rows, adjusted_rows = raw[symbol], adjusted[symbol]
        if raw_rows:
            write_daily_csv(out_dir / "raw", symbol, raw_rows)
        if adjusted_rows:
            write_daily_csv(out_dir / "adjusted", symbol, adjusted_rows)
        reports[symbol] = SymbolReport(
            rows_raw=len(raw_rows),
            rows_adjusted=len(adjusted_rows),
            first=raw_rows[0].session if raw_rows else None,
            last=raw_rows[-1].session if raw_rows else None,
            missing_sessions=tuple(missing_sessions(raw_rows, end, calendar)),
            large_moves=tuple(large_moves(adjusted_rows)),
            corporate_actions=tuple(corporate_actions(raw_rows, adjusted_rows)),
        )
    hashes = {
        series: DailyBarSource(out_dir / series, calendar).snapshot_hash
        for series in ("raw", "adjusted")
        if any((out_dir / series).glob("*.csv"))
    }
    report = FetchReport(start=start, end=end, snapshot_hashes=hashes, symbols=reports)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fetch_report.json").write_text(
        json.dumps(report.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report
