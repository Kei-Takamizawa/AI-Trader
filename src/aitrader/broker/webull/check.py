"""Operator-run sandbox probes; reports contain findings and metadata, never account data."""

import json
from collections.abc import Callable
from contextlib import suppress
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from time import sleep
from typing import Any
from uuid import uuid4

from aitrader.broker.port import (
    BrokerError,
    BrokerOrder,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
)
from aitrader.broker.webull import WebullBroker, parsing
from aitrader.clock import Clock
from aitrader.domain import IntentPurpose, OrderRequest, OrderStatus, OrderType, Side


class SandboxCheck:
    def __init__(
        self, broker: WebullBroker, clock: Clock, wait: Callable[[int], None] = sleep
    ) -> None:
        self.broker = broker
        self.clock = clock
        self.wait = wait
        self.calls: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self._recorded_calls = 0

    def observe(self, event: dict[str, Any]) -> None:
        self.calls.append(event)

    def _call[T](self, action: Callable[[], T]) -> T:
        # Leave headroom in every query window without retrying a transport operation.
        self.wait(2)
        return action()

    def _record(self, name: str, outcome: str, result: str, *, code: str | None = None) -> None:
        calls = self.calls[self._recorded_calls :]
        self._recorded_calls = len(self.calls)
        self.checks.append(
            {
                "check": name,
                "outcome": outcome,
                "result": result,
                "error_code": code,
                "latency_ms": str(sum(int(call["latency_ms"]) for call in calls)),
                "calls": calls,
            }
        )

    def _probe(self, name: str, action: Callable[[], Any]) -> None:
        try:
            self._call(action)
            self._record(name, "available", "pass")
        except BrokerError as exc:
            self._record(name, "unavailable", "fail", code=exc.code)

    def _accounts(self) -> None:
        rows = parsing.array(self.broker.call("accounts"), "accounts")
        # Compare in memory only. Account identifiers must never enter the report.
        account_id = self.broker.account().account_id
        if not any(parsing.obj(row, "accounts").get("account_id") == account_id for row in rows):
            raise BrokerUnavailableError("account_id")

    def _instrument(self) -> None:
        body = parsing.obj(
            self.broker.call("instruments", category="US_STOCK", symbols="SPY"), "instruments"
        )
        rows = parsing.array(body.get("data"), "data")
        if not any(
            isinstance(row, dict)
            and row.get("symbol") == "SPY"
            and isinstance(row.get("fractionable"), bool)
            and row.get("status") in {"OC", "CO", "NT"}
            for row in rows
        ):
            raise BrokerUnavailableError("instrument flags")

    def _confirm_cancel(self, client_order_id: str) -> BrokerOrder:
        # A fill may win the cancellation race. Confirm that no remainder is open.
        with suppress(OrderRejectedError):
            self._call(lambda: self.broker.cancel(client_order_id))
        order = self._call(lambda: self.broker.order_status(client_order_id))
        if order.status not in {OrderStatus.CANCELLED, OrderStatus.FILLED}:
            raise BrokerUnavailableError("cancel not confirmed")
        return order

    def _order(self, name: str, request: OrderRequest, *, whole: bool = False) -> bool:
        accepted = False
        try:
            try:
                if whole:
                    self._call(lambda: self.broker.preview(request))
                self._call(lambda: self.broker.place_once(request))
                accepted = True
            except OrderRejectedError as exc:
                self._record(
                    name,
                    "rejected" if whole else "rejected_as_expected",
                    "finding" if whole else "pass",
                    code=exc.code,
                )
                return False
            # An unexpectedly accepted fractional probe is cancelled immediately.
            if whole:
                order = self._call(lambda: self.broker.order_status(request.client_order_id))
                filled = order.filled_quantity > 0
            else:
                filled = False
            if not whole or order.status is not OrderStatus.FILLED:
                cancelled = self._confirm_cancel(request.client_order_id)
                filled = filled or cancelled.filled_quantity > 0
            if whole:
                self._record(
                    name,
                    "filled" if filled else "accepted_cancelled",
                    "finding" if filled else "pass",
                )
            else:
                self._record(name, "accepted_unexpectedly", "finding")
        except BrokerError as exc:
            # A submit timeout has an unknown outcome. Lookup and cancel only this ID.
            try:
                if not accepted:
                    self._call(lambda: self.broker.order_status(request.client_order_id))
                self._confirm_cancel(request.client_order_id)
            except BrokerError:
                pass
            self._record(name, "unavailable", "fail", code=exc.code)
        return accepted

    def run(self, out_dir: Path) -> tuple[Path, int]:
        self._probe("SB-1 accounts", self._accounts)
        self._probe("SB-1 balance", self.broker.account)
        self._probe("SB-1 positions", self.broker.positions)
        self._probe("SB-1 SPY flags", self._instrument)
        prefix = f"aitsb-{uuid4().hex[:8]}"
        request = OrderRequest(
            client_order_id=f"{prefix}-whole",
            intent_id=prefix,
            symbol="SPY",
            side=Side.BUY,
            purpose=IntentPurpose.ENTRY,
            order_type=OrderType.LIMIT,
            quantity=Decimal(1),
            limit_price=self.broker.settings.far_limit_price,
        )
        accepted = self._order("SB-2 whole", request, whole=True)
        for suffix, quantity in (
            ("fractional", Decimal("0.01000")),
            ("below-dollar", Decimal("0.00001")),
        ):
            self._order(
                f"SB-2 {suffix}",
                request.model_copy(
                    update={
                        "client_order_id": f"{prefix}-{suffix}",
                        "quantity": quantity,
                    }
                ),
            )
        if accepted:
            try:
                self._call(lambda: self.broker.place_once(request))
                self._confirm_cancel(request.client_order_id)
                self._record("SB-3 duplicate", "accepted_unexpectedly", "finding")
            except OrderRejectedError as exc:
                self._record("SB-3 duplicate", "rejected_as_expected", "pass", code=exc.code)
            except BrokerError as exc:
                self._record("SB-3 duplicate", "unavailable", "fail", code=exc.code)
        else:
            self._record("SB-3 duplicate", "skipped", "finding")
        try:
            self._call(lambda: self.broker.order_status(f"aitsb-unknown-{uuid4().hex[:8]}"))
            self._record("SB-3 unknown lookup", "found_unexpectedly", "finding")
        except OrderNotFoundError as exc:
            self._record("SB-3 unknown lookup", "not_found", "pass", code=exc.code)
        except BrokerError as exc:
            self._record("SB-3 unknown lookup", "unavailable", "fail", code=exc.code)
        answered = any(call["http_status"] is not None for call in self.calls)
        self._record(
            "SB-6 host", "answered" if answered else "unavailable", "pass" if answered else "fail"
        )
        report = {
            "date": self.clock.now().date().isoformat(),
            "sdk_version": version("webull-openapi-python-sdk"),
            "host": self.broker.host,
            "checks": self.checks,
            "calls": self.calls,
            "balances_present": any(
                call["operation"] == "balance" and call["shape"].get("cash_balance", False)
                for call in self.calls
            ),
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report['date']}.json"
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path, int(any(check["result"] == "fail" for check in self.checks))
