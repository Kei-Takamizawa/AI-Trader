"""Daily bars from CSV files, and the look-ahead guard (document 12 §2).

File layout: one ``<SYMBOL>.csv`` per instrument with the header
``date,open,high,low,close,volume`` and one row per session in ascending order. A bar's
timestamp is its session open (UTC); it becomes visible only after the session close.
"""

import bisect
import csv
import hashlib
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from aitrader.clock import Clock, TradingCalendar
from aitrader.domain import Bar


class DataError(Exception):
    pass


class LookAheadError(DataError):
    """Data from a session that had not closed yet at the clock's time was requested."""


class DailyBarSource:
    """All bars of a fixed set of CSV files. Simulator-side access; strategies get a view."""

    def __init__(
        self, directory: Path, calendar: TradingCalendar, symbols: Sequence[str] | None = None
    ) -> None:
        self._calendar = calendar
        files = sorted(directory.glob("*.csv"))
        if symbols is not None:
            wanted = set(symbols)
            missing = wanted - {f.stem for f in files}
            if missing:
                raise DataError(f"no data file for {', '.join(sorted(missing))}")
            files = [f for f in files if f.stem in wanted]
        if not files:
            raise DataError(f"no CSV files in {directory}")
        digest = hashlib.sha256()
        self._bars: dict[str, tuple[Bar, ...]] = {}
        self._sessions: dict[str, list[date]] = {}
        self._closes: dict[str, list[datetime]] = {}
        for path in files:
            content = path.read_bytes()
            digest.update(path.name.encode("utf-8") + b"\0" + content + b"\0")
            bars = self._parse(path.stem, path.name, content.decode("utf-8"))
            self._bars[path.stem] = bars
            self._sessions[path.stem] = [session_of(b) for b in bars]
            self._closes[path.stem] = [self._calendar.session_close(session_of(b)) for b in bars]
        self.snapshot_hash = digest.hexdigest()

    def _parse(self, symbol: str, name: str, text: str) -> tuple[Bar, ...]:
        reader = csv.DictReader(text.splitlines())
        expected = ["date", "open", "high", "low", "close", "volume"]
        if reader.fieldnames != expected:
            raise DataError(f"{name}: header must be {','.join(expected)}")
        bars: list[Bar] = []
        previous: date | None = None
        for line, row in enumerate(reader, start=2):
            try:
                session = date.fromisoformat(row["date"])
                if not self._calendar.is_session(session):
                    raise DataError(f"{session} is not a trading session")
                if previous is not None and session <= previous:
                    raise DataError("dates must be strictly increasing")
                bars.append(
                    Bar.model_validate(
                        {
                            "symbol": symbol,
                            "ts": self._calendar.session_open(session),
                            "open": row["open"],
                            "high": row["high"],
                            "low": row["low"],
                            "close": row["close"],
                            "volume": row["volume"],
                            "source": f"csv:{name}",
                        }
                    )
                )
            except (ValueError, DataError) as exc:
                raise DataError(f"{name} line {line}: {exc}") from exc
            previous = session
        return tuple(bars)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._bars)

    def bars(self, symbol: str) -> tuple[Bar, ...]:
        return self._bars.get(symbol, ())

    def bar_on(self, symbol: str, session: date) -> Bar | None:
        sessions = self._sessions.get(symbol, [])
        index = bisect.bisect_left(sessions, session)
        if index < len(sessions) and sessions[index] == session:
            return self._bars[symbol][index]
        return None

    def history(self, symbol: str, as_of: datetime) -> tuple[Bar, ...]:
        """Bars whose session closed at or before ``as_of``."""
        count = bisect.bisect_right(self._closes.get(symbol, []), as_of)
        return self._bars.get(symbol, ())[:count]

    def close_time(self, bar: Bar) -> datetime:
        return self._calendar.session_close(session_of(bar))


class MarketDataView:
    """What strategies and the risk engine may see: only sessions closed at ``clock.now()``."""

    def __init__(self, source: DailyBarSource, clock: Clock) -> None:
        self._source = source
        self._clock = clock

    def history(self, symbol: str) -> tuple[Bar, ...]:
        return self._source.history(symbol, self._clock.now())

    def bar(self, symbol: str, session: date) -> Bar:
        bar = self._source.bar_on(symbol, session)
        if bar is None:
            raise DataError(f"no {symbol} bar for {session}")
        if self._source.close_time(bar) > self._clock.now():
            raise LookAheadError(f"{symbol} {session} has not closed at {self._clock.now()}")
        return bar


def session_of(bar: Bar) -> date:
    """The session date of a daily bar (its open is on that UTC date)."""
    return bar.ts.date()
