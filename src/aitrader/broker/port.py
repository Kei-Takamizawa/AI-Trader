"""The broker port: what the order manager may ask of any broker, and the errors it can get.

Adapters (``sim``, later ``webull``) translate their broker's payloads and errors into these
types, so nothing outside an adapter sees broker-specific objects.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from aitrader.domain import (
    BrokerEnvironment,
    Fill,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    TradeCost,
)
from aitrader.domain.market import Symbol
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal, UtcDatetime


class BrokerCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fractional_orders: bool
    fractional_step: PositiveDecimal
    min_fractional_buy_usd: NonNegativeDecimal
    order_types: frozenset[OrderType]
    extended_hours: bool = False


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str = Field(min_length=1)
    environment: BrokerEnvironment
    ts: UtcDatetime
    cash_usd_settled: NonNegativeDecimal
    cash_usd_unsettled: NonNegativeDecimal
    cash_jpy: NonNegativeDecimal


class BrokerPosition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    quantity: PositiveDecimal
    cost_usd: NonNegativeDecimal


class BrokerOrder(BaseModel):
    """The broker's view of one order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str = Field(min_length=1)
    broker_order_id: str = Field(min_length=1)
    symbol: Symbol
    side: Side
    order_type: OrderType
    quantity: PositiveDecimal
    limit_price: PositiveDecimal | None = None
    status: OrderStatus
    filled_quantity: NonNegativeDecimal = Decimal(0)
    average_fill_price: PositiveDecimal | None = None
    reject_code: str | None = None
    updated_at: UtcDatetime


class BrokerOrderAck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str = Field(min_length=1)
    broker_order_id: str = Field(min_length=1)
    status: OrderStatus


class BrokerFill(BaseModel):
    """One execution with the costs the broker charged (or the cost model estimated)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fill: Fill
    costs: tuple[TradeCost, ...] = ()
    settles_on: date | None = None
    """Settlement date of a sale; None for buys."""


class BrokerError(Exception):
    """Base class of every error a broker adapter raises."""


class BrokerUnavailableError(BrokerError):
    """Timeout, lost connection or server error: the outcome of the call is unknown."""


class OrderRejectedError(BrokerError):
    """The broker refused the request. ``known`` is False for codes not in the known-codes
    table (ORD-ERR-2)."""

    def __init__(self, code: str, message: str = "", *, known: bool = True) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.known = known


class OrderNotFoundError(BrokerError):
    pass


class RateLimitedError(BrokerError):
    """The broker refused the call because of its rate limit; the request was not processed."""


class BrokerAuthError(BrokerError):
    """401, 403 or an invalid token: the request was not processed (AUTH-4)."""


@runtime_checkable
class BrokerPort(Protocol):
    @property
    def environment(self) -> BrokerEnvironment: ...

    def capabilities(self) -> BrokerCapabilities: ...

    def account(self) -> AccountSnapshot: ...

    def positions(self) -> list[BrokerPosition]: ...

    def open_orders(self) -> list[BrokerOrder]: ...

    def submit(self, request: OrderRequest) -> BrokerOrderAck:
        """Place an order. Idempotent on ``client_order_id``: the same ID returns the same
        order."""
        ...

    def cancel(self, client_order_id: str) -> None: ...

    def order_status(self, client_order_id: str) -> BrokerOrder:
        """Raises :class:`OrderNotFoundError` when the broker has no such order."""
        ...

    def fills_since(self, ts: datetime) -> list[BrokerFill]:
        """Fills with a timestamp at or after ``ts`` (callers de-duplicate by fill ID)."""
        ...
