"""The order manager: from an approved intent to a booked fill (document 03 §5).

- The order record is written **before** the broker call (write-ahead).
- A timeout makes the order UNKNOWN; it is then looked up by ``client_order_id`` and never
  resubmitted blindly. Not found after the reconciliation window → NOT_FOUND, and only then one
  explicit retry is allowed (DUP-2).
- Fills are booked in the engine ledger exactly once. A fill that does not match its order
  record is not booked; it is reported (ORD-ERR-4) for reconciliation.
- A terminal broker status is adopted only when the booked fills match the broker's filled
  quantity, so late fills never arrive on a closed record.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from aitrader.broker.port import (
    BrokerAuthError,
    BrokerFill,
    BrokerOrder,
    BrokerPort,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitedError,
)
from aitrader.clock import Clock
from aitrader.domain import (
    Instrument,
    OrderIntent,
    OrderRequest,
    OrderStatus,
    OrderType,
    RiskDecision,
    make_client_order_id,
)
from aitrader.domain.types import UtcDatetime
from aitrader.execution.gate import PROTECTIVE, GateContext, GateResult, OrderGate
from aitrader.execution.states import IllegalOrderTransitionError
from aitrader.execution.store import OrderRecord, OrderStore
from aitrader.portfolio import Ledger, LedgerError

MAX_ATTEMPTS = 2
"""The first attempt and at most one retry after NOT_FOUND (DUP-2)."""


class ExecutionEventKind(StrEnum):
    GATE_REJECTED = "GATE_REJECTED"
    SUBMITTED = "SUBMITTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NOT_FOUND"
    STATUS_CHANGED = "STATUS_CHANGED"
    FILL_BOOKED = "FILL_BOOKED"
    MISMATCH = "MISMATCH"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_FAILED = "AUTH_FAILED"


class ExecutionEvent(BaseModel):
    """Something the halt logic and the operator need to know. ``rule_id`` names the stop rule
    of document 05 §16 it triggers, if any."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: UtcDatetime
    kind: ExecutionEventKind
    client_order_id: str | None = None
    rule_id: str | None = None
    detail: str = ""


class RetryNotAllowedError(Exception):
    pass


class OrderNotActiveError(Exception):
    pass


@dataclass(frozen=True)
class SubmitResult:
    record: OrderRecord | None
    gate: GateResult
    events: tuple[ExecutionEvent, ...] = field(default=())


class OrderManager:
    def __init__(
        self,
        broker: BrokerPort,
        gate: OrderGate,
        store: OrderStore,
        ledger: Ledger,
        clock: Clock,
        *,
        not_found_after: timedelta = timedelta(minutes=2),
        fills_from: datetime | None = None,
    ) -> None:
        self._broker = broker
        self._gate = gate
        self._store = store
        self._ledger = ledger
        self._clock = clock
        self._not_found_after = not_found_after
        self._fills_cursor = fills_from or clock.now()

    # ------------------------------------------------------------------ placing orders

    def submit(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        instrument: Instrument,
        context: GateContext,
        *,
        order_type: OrderType = OrderType.LIMIT,
        limit_price: Decimal | None = None,
    ) -> SubmitResult:
        """First attempt for an intent. An intent with any earlier order is refused: rejected
        intents are dropped, and NOT_FOUND ones go through :meth:`retry_not_found`."""
        if self._store.for_intent(intent.intent_id):
            raise RetryNotAllowedError(f"intent {intent.intent_id} already has orders")
        return self._place(intent, decision, instrument, context, 1, order_type, limit_price)

    def retry_not_found(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        instrument: Instrument,
        context: GateContext,
        *,
        order_type: OrderType = OrderType.LIMIT,
        limit_price: Decimal | None = None,
    ) -> SubmitResult:
        """The one retry allowed after the broker confirmed the previous attempt absent."""
        records = self._store.for_intent(intent.intent_id)
        if (
            not records
            or records[-1].status is not OrderStatus.NOT_FOUND
            or len(records) >= MAX_ATTEMPTS
        ):
            raise RetryNotAllowedError("a retry needs a NOT_FOUND first attempt")
        attempt = len(records) + 1
        return self._place(intent, decision, instrument, context, attempt, order_type, limit_price)

    def _place(
        self,
        intent: OrderIntent,
        decision: RiskDecision,
        instrument: Instrument,
        context: GateContext,
        attempt: int,
        order_type: OrderType,
        limit_price: Decimal | None,
    ) -> SubmitResult:
        price = limit_price if limit_price is not None else intent.limit_price
        request = OrderRequest(
            client_order_id=make_client_order_id(intent.intent_id, attempt),
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            purpose=intent.purpose,
            order_type=order_type,
            quantity=decision.approved_quantity,
            limit_price=price if order_type is OrderType.LIMIT else None,
        )
        gate = self._gate.check(request, decision, instrument, context)
        now = self._clock.now()
        if not gate.passed:
            event = self._event(
                ExecutionEventKind.GATE_REJECTED,
                request.client_order_id,
                detail=",".join(gate.failed_rules),
            )
            return SubmitResult(None, gate, (event,))

        record = OrderRecord(
            request=request,
            attempt=attempt,
            status=OrderStatus.PENDING_SUBMIT,
            created_at=now,
            updated_at=now,
        )
        self._store.insert(record, "WRITE_AHEAD")
        cid = request.client_order_id
        try:
            ack = self._broker.submit(request)
        except BrokerUnavailableError as exc:
            record = self._move(record, OrderStatus.UNKNOWN, "SUBMIT_TIMEOUT", last_error=str(exc))
            return SubmitResult(
                record, gate, (self._event(ExecutionEventKind.UNKNOWN, cid, "ORD-ERR-3", str(exc)),)
            )
        except RateLimitedError as exc:
            record = self._move(record, OrderStatus.REJECTED, "RATE_LIMITED", last_error=str(exc))
            return SubmitResult(
                record,
                gate,
                (self._event(ExecutionEventKind.RATE_LIMITED, cid, "ORD-ERR-5", str(exc)),),
            )
        except BrokerAuthError as exc:
            record = self._move(record, OrderStatus.REJECTED, "AUTH_FAILED", last_error=str(exc))
            return SubmitResult(
                record,
                gate,
                (self._event(ExecutionEventKind.AUTH_FAILED, cid, "AUTH-4", str(exc)),),
            )
        except OrderRejectedError as exc:
            record = self._move(record, OrderStatus.REJECTED, "REJECTED", last_error=str(exc))
            rule = "ORD-ERR-1" if exc.known else "ORD-ERR-2"
            return SubmitResult(
                record, gate, (self._event(ExecutionEventKind.REJECTED, cid, rule, exc.code),)
            )
        record = self._move(
            record, OrderStatus.SUBMITTED, "ACK", broker_order_id=ack.broker_order_id
        )
        return SubmitResult(record, gate, (self._event(ExecutionEventKind.SUBMITTED, cid),))

    # ------------------------------------------------------------------ cancelling

    def cancel(self, client_order_id: str) -> list[ExecutionEvent]:
        record = self._store.get(client_order_id)
        if record is None or record.status not in (
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            raise OrderNotActiveError(f"{client_order_id} cannot be cancelled now")
        self._move(record, OrderStatus.CANCEL_PENDING, "CANCEL_REQUESTED")
        events = [self._event(ExecutionEventKind.CANCEL_REQUESTED, client_order_id)]
        try:
            self._broker.cancel(client_order_id)
        except BrokerUnavailableError as exc:
            events.append(
                self._event(
                    ExecutionEventKind.BROKER_UNAVAILABLE, client_order_id, "ORD-ERR-3", str(exc)
                )
            )
        except OrderNotFoundError:
            events.append(
                self._event(
                    ExecutionEventKind.MISMATCH,
                    client_order_id,
                    "ORD-ERR-4",
                    "the broker does not know an order we submitted",
                )
            )
        return events

    def cancel_non_protective(self) -> list[ExecutionEvent]:
        """Kill switch (document 05 §11): cancel every open entry and ordinary exit order.
        Protective stop exits and flatten orders stay."""
        events: list[ExecutionEvent] = []
        for record in self._store.active():
            if record.request.purpose not in PROTECTIVE and record.status in (
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
            ):
                events.extend(self.cancel(record.client_order_id))
        return events

    # ------------------------------------------------------------------ synchronising

    def sync(self) -> list[ExecutionEvent]:
        """Recover write-ahead records, book new fills, then adopt broker statuses."""
        events: list[ExecutionEvent] = []
        for record in self._store.active():
            if record.status is OrderStatus.PENDING_SUBMIT:
                # The process stopped between the write-ahead record and the broker's answer.
                self._move(
                    record, OrderStatus.UNKNOWN, "RECOVERED", last_error="no broker answer recorded"
                )
                events.append(
                    self._event(ExecutionEventKind.UNKNOWN, record.client_order_id, "ORD-ERR-3")
                )
        try:
            fills = self._broker.fills_since(self._fills_cursor)
        except BrokerUnavailableError as exc:
            events.append(
                self._event(ExecutionEventKind.BROKER_UNAVAILABLE, None, "ORD-ERR-3", str(exc))
            )
            return events
        for broker_fill in sorted(fills, key=lambda f: (f.fill.ts, f.fill.fill_id)):
            events.extend(self._book_fill(broker_fill))
            self._fills_cursor = max(self._fills_cursor, broker_fill.fill.ts)
        for record in self._store.active():
            events.extend(self._sync_status(record))
        return events

    def _sync_status(self, record: OrderRecord) -> list[ExecutionEvent]:
        cid = record.client_order_id
        try:
            order = self._broker.order_status(cid)
        except BrokerUnavailableError as exc:
            return [self._event(ExecutionEventKind.BROKER_UNAVAILABLE, cid, "ORD-ERR-3", str(exc))]
        except OrderNotFoundError:
            if record.status is not OrderStatus.UNKNOWN:
                return [
                    self._event(
                        ExecutionEventKind.MISMATCH,
                        cid,
                        "ORD-ERR-4",
                        f"the broker does not know an order in state {record.status.value}",
                    )
                ]
            if self._clock.now() - record.created_at < self._not_found_after:
                return []
            self._move(record, OrderStatus.NOT_FOUND, "NOT_FOUND")
            return [self._event(ExecutionEventKind.NOT_FOUND, cid)]
        return self._adopt(record, order)

    def _adopt(self, record: OrderRecord, order: BrokerOrder) -> list[ExecutionEvent]:
        cid = record.client_order_id
        if order.filled_quantity < record.filled_quantity:
            return [
                self._event(
                    ExecutionEventKind.MISMATCH,
                    cid,
                    "ORD-ERR-4",
                    f"booked {record.filled_quantity}, broker reports {order.filled_quantity}",
                )
            ]
        target = order.status
        if target.is_terminal and order.filled_quantity != record.filled_quantity:
            target = (
                OrderStatus.PARTIALLY_FILLED
                if record.filled_quantity > 0
                else OrderStatus.SUBMITTED
            )
            if record.status is OrderStatus.CANCEL_PENDING:
                target = OrderStatus.CANCEL_PENDING  # wait for the remaining fills to be booked
        if record.status is OrderStatus.CANCEL_PENDING and target in (
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            target = OrderStatus.CANCEL_PENDING  # the broker has not processed the cancel yet
        if target is record.status and order.broker_order_id == record.broker_order_id:
            return []
        try:
            self._move(record, target, "BROKER_STATUS", broker_order_id=order.broker_order_id)
        except IllegalOrderTransitionError as exc:
            return [self._event(ExecutionEventKind.MISMATCH, cid, "ORD-ERR-4", str(exc))]
        if target is OrderStatus.REJECTED:
            return [
                self._event(ExecutionEventKind.REJECTED, cid, "ORD-ERR-1", order.reject_code or "")
            ]
        return [self._event(ExecutionEventKind.STATUS_CHANGED, cid, detail=target.value)]

    def _book_fill(self, broker_fill: BrokerFill) -> list[ExecutionEvent]:
        fill = broker_fill.fill
        cid = fill.client_order_id
        if self._ledger.entry(f"fill:{fill.fill_id}") is not None:
            return []  # re-delivery (cursor overlap or a duplicate event): booked already
        record = self._store.get(cid)
        problem = None
        if record is None:
            problem = "fill for an unknown order"
        elif not record.is_active:
            problem = f"fill for an order in state {record.status.value}"
        elif record.request.symbol != fill.symbol or record.request.side is not fill.side:
            problem = "fill symbol or side differs from the order"
        elif record.filled_quantity + fill.quantity > record.request.quantity:
            problem = "fills exceed the order quantity"
        if problem is None and record is not None:
            try:
                self._ledger.record_fill(fill, broker_fill.costs, settles_on=broker_fill.settles_on)
            except (LedgerError, ValueError) as exc:
                problem = f"ledger refused the fill: {exc}"
        if problem is not None or record is None:
            return [
                self._event(
                    ExecutionEventKind.MISMATCH, cid, "ORD-ERR-4", f"{fill.fill_id}: {problem}"
                )
            ]
        filled = record.filled_quantity + fill.quantity
        if filled == record.request.quantity:
            target = OrderStatus.FILLED
        elif record.status is OrderStatus.CANCEL_PENDING:
            target = OrderStatus.CANCEL_PENDING
        else:
            target = OrderStatus.PARTIALLY_FILLED
        self._move(record, target, "FILL", filled_quantity=filled)
        return [self._event(ExecutionEventKind.FILL_BOOKED, cid, detail=fill.fill_id)]

    # ------------------------------------------------------------------ helpers

    def _move(
        self, record: OrderRecord, status: OrderStatus, event: str, **changes: object
    ) -> OrderRecord:
        updated = record.moved_to(status, self._clock.now(), **changes)
        self._store.update(updated, event, str(changes.get("last_error", "")))
        return updated

    def _event(
        self,
        kind: ExecutionEventKind,
        client_order_id: str | None,
        rule_id: str | None = None,
        detail: str = "",
    ) -> ExecutionEvent:
        return ExecutionEvent(
            ts=self._clock.now(),
            kind=kind,
            client_order_id=client_order_id,
            rule_id=rule_id,
            detail=detail,
        )
