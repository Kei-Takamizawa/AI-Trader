"""NYSE calendar: holidays, half days, daylight-saving time and T+1 settlement."""

from datetime import UTC, date, datetime

import pytest

from aitrader.clock import CALENDAR_END, CALENDAR_START, CalendarError, TradingCalendar

CAL = TradingCalendar()


@pytest.mark.parametrize(
    ("day", "is_session"),
    [
        (date(2024, 7, 4), False),  # Independence Day
        (date(2024, 11, 28), False),  # Thanksgiving
        (date(2024, 12, 25), False),
        (date(2024, 7, 6), False),  # Saturday
        (date(2024, 7, 5), True),
        (date(2026, 10, 23), True),
    ],
)
def test_sessions_follow_the_exchange_calendar(day: date, is_session: bool) -> None:
    assert CAL.is_session(day) is is_session


def test_open_and_close_follow_daylight_saving_time() -> None:
    assert CAL.session_open(date(2024, 7, 5)) == datetime(2024, 7, 5, 13, 30, tzinfo=UTC)
    assert CAL.session_close(date(2024, 7, 5)) == datetime(2024, 7, 5, 20, 0, tzinfo=UTC)
    assert CAL.session_open(date(2024, 12, 2)) == datetime(2024, 12, 2, 14, 30, tzinfo=UTC)


@pytest.mark.parametrize("day", [date(2024, 7, 3), date(2024, 11, 29), date(2024, 12, 24)])
def test_half_days_close_at_13_new_york(day: date) -> None:
    close = CAL.session_close(day)
    assert close.hour in (17, 18)  # 13:00 New York in summer / winter
    assert (close - CAL.session_open(day)).total_seconds() == 3.5 * 3600


def test_next_and_previous_session() -> None:
    assert CAL.next_session(date(2024, 7, 3)) == date(2024, 7, 5)
    assert CAL.next_session(date(2024, 7, 5)) == date(2024, 7, 8)
    assert CAL.previous_session(date(2024, 7, 5)) == date(2024, 7, 3)
    assert CAL.settlement_date(date(2024, 7, 3)) == date(2024, 7, 5)


def test_sessions_in_a_range() -> None:
    assert len(CAL.sessions(date(2023, 1, 1), date(2023, 12, 31))) == 250
    assert CAL.sessions(date(2024, 7, 4), date(2024, 7, 4)) == []


def test_regular_hours() -> None:
    day = date(2024, 7, 5)
    assert CAL.is_open(datetime(2024, 7, 5, 13, 30, tzinfo=UTC))
    assert not CAL.is_open(datetime(2024, 7, 5, 13, 29, tzinfo=UTC))
    assert not CAL.is_open(CAL.session_close(day))
    assert not CAL.is_open(datetime(2024, 7, 4, 15, 0, tzinfo=UTC))


def test_bounds_and_non_sessions_are_refused() -> None:
    with pytest.raises(CalendarError, match="outside"):
        CAL.is_session(date(2004, 12, 31))
    with pytest.raises(CalendarError, match="outside"):
        CAL.sessions(CALENDAR_START, date(2031, 1, 2))
    with pytest.raises(CalendarError, match="not a session"):
        CAL.session_open(date(2024, 7, 4))
    with pytest.raises(CalendarError, match="not a session"):
        CAL.session_close(date(2024, 7, 4))
    assert CAL.is_session(CALENDAR_END) in (True, False)
