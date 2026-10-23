"""Order records with write-ahead semantics and an append-only event log.

The record of an order is saved **before** the broker call, so after a crash the store tells
the reconciler what may exist at the broker. At most one order per intent is active at a time.
"""

from datetime import datetime
from decimal import Decimal
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field

from aitrader.domain import OrderRequest, OrderStatus
from aitrader.domain.types import NonNegativeDecimal, UtcDatetime
from aitrader.execution.states import check_transition


class OrderRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: OrderRequest
    attempt: int = Field(ge=1)
    status: OrderStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime
    broker_order_id: str | None = None
    filled_quantity: NonNegativeDecimal = Decimal(0)
    last_error: str = ""

    @property
    def client_order_id(self) -> str:
        return self.request.client_order_id

    @property
    def intent_id(self) -> str:
        return self.request.intent_id

    @property
    def is_active(self) -> bool:
        return not self.status.is_terminal

    @property
    def remaining(self) -> Decimal:
        return self.request.quantity - self.filled_quantity

    def moved_to(self, status: OrderStatus, ts: datetime, **changes: object) -> Self:
        """A copy in ``status``; refuses transitions the state machine does not allow."""
        check_transition(self.status, status)
        return self.model_copy(update={"status": status, "updated_at": ts, **changes})


class OrderEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: UtcDatetime
    client_order_id: str
    event: str
    from_status: OrderStatus | None
    to_status: OrderStatus
    detail: str = ""


class DuplicateOrderError(Exception):
    """A second record for a client order ID, or a second active order for an intent."""


class OrderStore(Protocol):
    def insert(self, record: OrderRecord, event: str) -> None: ...

    def update(self, record: OrderRecord, event: str, detail: str = "") -> None: ...

    def get(self, client_order_id: str) -> OrderRecord | None: ...

    def for_intent(self, intent_id: str) -> list[OrderRecord]: ...

    def active(self) -> list[OrderRecord]: ...

    def events(self, client_order_id: str | None = None) -> list[OrderEvent]: ...


class InMemoryOrderStore:
    def __init__(self) -> None:
        self._records: dict[str, OrderRecord] = {}
        self._events: list[OrderEvent] = []

    def insert(self, record: OrderRecord, event: str) -> None:
        if record.client_order_id in self._records:
            raise DuplicateOrderError(f"order {record.client_order_id} already exists")
        if any(r.is_active for r in self.for_intent(record.intent_id)):
            raise DuplicateOrderError(f"intent {record.intent_id} already has an active order")
        self._records[record.client_order_id] = record
        self._log(record, event, None, record.last_error)

    def update(self, record: OrderRecord, event: str, detail: str = "") -> None:
        current = self._records.get(record.client_order_id)
        if current is None:
            raise KeyError(record.client_order_id)
        check_transition(current.status, record.status)
        self._records[record.client_order_id] = record
        self._log(record, event, current.status, detail)

    def get(self, client_order_id: str) -> OrderRecord | None:
        return self._records.get(client_order_id)

    def for_intent(self, intent_id: str) -> list[OrderRecord]:
        return sorted(
            (r for r in self._records.values() if r.intent_id == intent_id),
            key=lambda r: r.attempt,
        )

    def active(self) -> list[OrderRecord]:
        return sorted(
            (r for r in self._records.values() if r.is_active),
            key=lambda r: (r.created_at, r.client_order_id),
        )

    def events(self, client_order_id: str | None = None) -> list[OrderEvent]:
        return [e for e in self._events if client_order_id in (None, e.client_order_id)]

    def _log(
        self, record: OrderRecord, event: str, from_status: OrderStatus | None, detail: str
    ) -> None:
        self._events.append(
            OrderEvent(
                ts=record.updated_at,
                client_order_id=record.client_order_id,
                event=event,
                from_status=from_status,
                to_status=record.status,
                detail=detail,
            )
        )
