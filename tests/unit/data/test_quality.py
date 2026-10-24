"""Data quality gate: completeness (DQ-2) and abnormal daily moves (DQ-3)."""

from datetime import date
from decimal import Decimal

from aitrader.clock import TradingCalendar
from aitrader.data import DataQualityGate
from aitrader.domain import Bar

CAL = TradingCalendar()
GATE = DataQualityGate(CAL, lookback_sessions=5)


def bars(sessions: list[date], closes: list[str] | None = None) -> list[Bar]:
    closes = closes or ["100"] * len(sessions)
    return [
        Bar(
            symbol="SPY",
            ts=CAL.session_open(day),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(1),
        )
        for day, close in zip(sessions, closes, strict=True)
    ]


SESSIONS = CAL.sessions(date(2024, 6, 24), date(2024, 7, 5))  # ends Friday 5 July


def test_complete_recent_history_passes() -> None:
    assert GATE.findings(bars(SESSIONS), date(2024, 7, 8)) == frozenset()


def test_a_missing_session_fails_dq2() -> None:
    history = bars([s for s in SESSIONS if s != date(2024, 7, 2)])
    assert GATE.findings(history, date(2024, 7, 8)) == frozenset({"DQ-2"})


def test_stale_history_fails_dq2() -> None:
    assert "DQ-2" in GATE.findings(bars(SESSIONS[:-1]), date(2024, 7, 8))


def test_gaps_outside_the_lookback_window_are_ignored() -> None:
    history = bars([s for s in SESSIONS if s != SESSIONS[0]])
    assert GATE.findings(history, date(2024, 7, 8)) == frozenset()


def test_no_history_fails_dq2() -> None:
    assert GATE.findings([], date(2024, 7, 8)) == frozenset({"DQ-2"})


def test_daily_move_above_25_percent_fails_dq3() -> None:
    closes = ["100"] * (len(SESSIONS) - 1) + ["125.01"]
    assert GATE.findings(bars(SESSIONS, closes), date(2024, 7, 8)) == frozenset({"DQ-3"})
    closes[-1] = "125"
    assert GATE.findings(bars(SESSIONS, closes), date(2024, 7, 8)) == frozenset()


def test_a_single_bar_has_no_daily_move() -> None:
    day = date(2024, 7, 5)
    assert (
        DataQualityGate(CAL, lookback_sessions=1).findings(bars([day]), date(2024, 7, 8))
        == frozenset()
    )
