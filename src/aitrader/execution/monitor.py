"""Order errors drive the halt state machine (document 05 §16.3 and AUTH-4)."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from aitrader.execution.manager import ExecutionEvent
from aitrader.risk.halt import HaltReason, HaltStateMachine, HaltTransition

MAX_REJECTIONS_PER_SESSION = 2
"""The second rejection in one session is ORD-ERR-2."""


class OrderErrorMonitor:
    def __init__(
        self, halt: HaltStateMachine, *, rate_limit_window: timedelta = timedelta(minutes=5)
    ) -> None:
        self._halt = halt
        self._rate_limit_window = rate_limit_window
        self._rejections: dict[date, int] = defaultdict(int)
        self._last_rate_limit: datetime | None = None

    def observe(self, events: Iterable[ExecutionEvent]) -> list[HaltTransition]:
        transitions = []
        for event in events:
            transition = self._observe(event)
            if transition is not None:
                transitions.append(transition)
        return transitions

    def _observe(self, event: ExecutionEvent) -> HaltTransition | None:
        rule = event.rule_id
        detail = f"{rule}: {event.client_order_id or '-'} {event.detail}".strip()
        if rule == "ORD-ERR-1":
            session = event.ts.date()  # regular hours fall within one UTC date
            self._rejections[session] += 1
            if self._rejections[session] >= MAX_REJECTIONS_PER_SESSION:
                return self._halt.halt(HaltReason.ORDER_ERRORS, event.ts, f"ORD-ERR-2 ({detail})")
            return None
        if rule == "ORD-ERR-2":
            return self._halt.halt(HaltReason.ORDER_ERRORS, event.ts, detail)
        if rule == "ORD-ERR-3":
            return self._halt.degrade(HaltReason.BROKER_UNAVAILABLE, event.ts, detail)
        if rule == "ORD-ERR-4":
            return self._halt.halt(HaltReason.RECONCILIATION_MISMATCH, event.ts, detail)
        if rule == "ORD-ERR-5":
            previous, self._last_rate_limit = self._last_rate_limit, event.ts
            if previous is not None and event.ts - previous <= self._rate_limit_window:
                return self._halt.degrade(HaltReason.RATE_LIMITED, event.ts, detail)
            return None
        if rule == "AUTH-4":
            return self._halt.halt(HaltReason.AUTHENTICATION, event.ts, detail)
        return None
