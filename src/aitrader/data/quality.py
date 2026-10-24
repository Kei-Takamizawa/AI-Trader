"""Data quality gate for daily bars (document 05 §4). Findings block new entries per symbol.

Implemented here: DQ-2 (completeness against the exchange calendar) and the daily-move part of
DQ-3 (an ETF move above 25% needs a second source). Malformed bars never get this far: they are
refused when loaded. Cross-source checks (DQ-4) and corporate actions (DQ-5) come with the
market-data task.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from aitrader.clock import TradingCalendar
from aitrader.data.bars import session_of
from aitrader.domain import Bar


class DataQualityGate:
    def __init__(
        self,
        calendar: TradingCalendar,
        *,
        lookback_sessions: int = 252,
        max_daily_move: Decimal = Decimal("0.25"),
    ) -> None:
        self._calendar = calendar
        self._lookback = lookback_sessions
        self._max_move = max_daily_move

    def findings(self, history: Sequence[Bar], before: date) -> frozenset[str]:
        """Rule IDs that fail for a symbol whose closed bars are ``history``, judged on the
        morning of session ``before``."""
        if not history:
            return frozenset({"DQ-2"})
        failures = set()
        last_session = self._calendar.previous_session(before)
        first_session = session_of(history[0])
        expected = self._calendar.sessions(first_session, last_session)[-self._lookback :]
        present = {session_of(b) for b in history[-len(expected) - 1 :]}
        if any(session not in present for session in expected):
            failures.add("DQ-2")
        if len(history) >= 2:
            move = abs(history[-1].close / history[-2].close - 1)
            if move > self._max_move:
                failures.add("DQ-3")
        return frozenset(failures)
