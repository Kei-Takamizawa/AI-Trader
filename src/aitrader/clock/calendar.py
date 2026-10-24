"""NYSE trading sessions: holidays, half days and daylight-saving time (``exchange_calendars``).

The calendar is built for fixed bounds so that results never depend on the date a program runs.
"""

from datetime import UTC, date, datetime, timedelta
from functools import cache

import exchange_calendars as xcals  # type: ignore[import-untyped]
import pandas as pd  # type: ignore[import-untyped]

CALENDAR_START = date(2005, 1, 3)
CALENDAR_END = date(2030, 12, 31)


class CalendarError(Exception):
    pass


@cache
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar(
        "XNYS", start=pd.Timestamp(CALENDAR_START), end=pd.Timestamp(CALENDAR_END)
    )


def _to_utc(ts: pd.Timestamp) -> datetime:
    value: datetime = ts.to_pydatetime()
    return value.astimezone(UTC)


class TradingCalendar:
    """Regular sessions of the New York Stock Exchange (XNYS)."""

    def __init__(self) -> None:
        self._calendar = _xnys()

    def _check(self, day: date) -> pd.Timestamp:
        if not CALENDAR_START <= day <= CALENDAR_END:
            raise CalendarError(f"{day} is outside {CALENDAR_START} .. {CALENDAR_END}")
        return pd.Timestamp(day)

    def is_session(self, day: date) -> bool:
        return bool(self._calendar.is_session(self._check(day)))

    def sessions(self, start: date, end: date) -> list[date]:
        """Sessions from ``start`` to ``end`` inclusive."""
        values = self._calendar.sessions_in_range(self._check(start), self._check(end))
        return [ts.date() for ts in values]

    def next_session(self, day: date) -> date:
        """The first session strictly after ``day``."""
        following = day + timedelta(days=1)
        while not self.is_session(following):
            following += timedelta(days=1)
        return following

    def previous_session(self, day: date) -> date:
        """The last session strictly before ``day``."""
        earlier = day - timedelta(days=1)
        while not self.is_session(earlier):
            earlier -= timedelta(days=1)
        return earlier

    def session_open(self, day: date) -> datetime:
        if not self.is_session(day):
            raise CalendarError(f"{day} is not a session")
        return _to_utc(self._calendar.session_open(pd.Timestamp(day)))

    def session_close(self, day: date) -> datetime:
        """Regular close, 13:00 New York time on half days."""
        if not self.is_session(day):
            raise CalendarError(f"{day} is not a session")
        return _to_utc(self._calendar.session_close(pd.Timestamp(day)))

    def is_open(self, ts: datetime) -> bool:
        """True during regular trading hours (HC-7)."""
        day = ts.astimezone(UTC).date()
        if not self.is_session(day):
            return False
        return self.session_open(day) <= ts < self.session_close(day)

    def settlement_date(self, trade_date: date) -> date:
        """T+1: the next session after the trade date."""
        return self.next_session(trade_date)
