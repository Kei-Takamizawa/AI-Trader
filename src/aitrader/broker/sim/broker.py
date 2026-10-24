"""SimBroker: the broker for BACKTEST and PAPER (document 02 §5, document 12 §4).

It keeps its own books in a :class:`~aitrader.portfolio.Ledger`, so cash-account rules and T+1
settlement work as in the engine, and the engine's ledger can be reconciled against it. Orders
fill from quotes: marketable limits never fill beyond their limit (no price improvement is
assumed beyond the quote plus slippage), market orders fill at the quote plus slippage.
Faults can be injected for tests.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from aitrader.broker.port import (
    AccountSnapshot,
    BrokerAuthError,
    BrokerCapabilities,
    BrokerFill,
    BrokerOrder,
    BrokerOrderAck,
    BrokerPosition,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitedError,
)
from aitrader.clock import Clock
from aitrader.domain import (
    BrokerEnvironment,
    Currency,
    Fill,
    Money,
    OrderRequest,
    OrderStatus,
    OrderType,
    Quote,
    Side,
    TradeCost,
)
from aitrader.fees import WEBULL_JP, FeeSchedule, order_fees, total_order_fees
from aitrader.portfolio import Ledger

BPS = Decimal(10_000)
CENT = Decimal("0.01")


class SimOperation(StrEnum):
    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    STATUS = "STATUS"


class SimFault(StrEnum):
    TIMEOUT_BEFORE_ACCEPT = "TIMEOUT_BEFORE_ACCEPT"
    """The call fails and nothing happened at the broker."""
    TIMEOUT_AFTER_ACCEPT = "TIMEOUT_AFTER_ACCEPT"
    """The broker processed the call, but the caller only sees a timeout."""
    REJECT = "REJECT"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH_FAILURE = "AUTH_FAILURE"


@dataclass(frozen=True)
class _Fault:
    fault: SimFault
    code: str
    known: bool


@dataclass
class _SimOrder:
    request: OrderRequest
    broker_order_id: str
    status: OrderStatus
    updated_at: datetime
    filled_quantity: Decimal = Decimal(0)
    fill_value: Decimal = Decimal(0)
    reject_code: str | None = None
    fill_count: int = 0
    reserve_price: Decimal = Decimal(0)
    """Limit price of an open BUY, used to reserve buying power; zero for sells."""

    @property
    def remaining(self) -> Decimal:
        return self.request.quantity - self.filled_quantity

    def snapshot(self) -> BrokerOrder:
        average = self.fill_value / self.filled_quantity if self.filled_quantity else None
        return BrokerOrder(
            client_order_id=self.request.client_order_id,
            broker_order_id=self.broker_order_id,
            symbol=self.request.symbol,
            side=self.request.side,
            order_type=self.request.order_type,
            quantity=self.request.quantity,
            limit_price=self.request.limit_price,
            status=self.status,
            filled_quantity=self.filled_quantity,
            average_fill_price=average,
            reject_code=self.reject_code,
            updated_at=self.updated_at,
        )


def next_weekday(day: date) -> date:
    """T+1 ignoring holidays; the exchange calendar replaces this in the backtest task."""
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


class SimBroker:
    def __init__(
        self,
        clock: Clock,
        *,
        fees: FeeSchedule = WEBULL_JP,
        slippage_bps: Decimal = Decimal(5),
        account_id: str = "SIM-1",
        settlement_date: Callable[[date], date] = next_weekday,
    ) -> None:
        self._clock = clock
        self._fees = fees
        self._slippage = slippage_bps / BPS
        self._account_id = account_id
        self._settlement_date = settlement_date
        self._ledger = Ledger()
        self._orders: dict[str, _SimOrder] = {}
        self._fills: list[BrokerFill] = []
        self._faults: dict[SimOperation, deque[_Fault]] = {op: deque() for op in SimOperation}
        self._duplicate_fills = False
        self._funding = 0

    # ------------------------------------------------------------------ test controls

    @property
    def ledger(self) -> Ledger:
        """The broker's own books (read only by convention)."""
        return self._ledger

    def fund(self, amount: Money) -> None:
        self._funding += 1
        self._ledger.deposit(f"fund-{self._funding}", self._clock.now(), amount)

    def credit_dividend(
        self, entry_id: str, symbol: str, gross: Money, us_withholding: Money, jp_withholding: Money
    ) -> None:
        """Credit settled cash on the ex-date; actual dividends are paid later."""
        self._ledger.record_dividend(
            entry_id, self._clock.now(), symbol, gross, us_withholding, jp_withholding
        )

    def inject(
        self, operation: SimOperation, fault: SimFault, *, code: str = "", known: bool = True
    ) -> None:
        """Make the next call of ``operation`` fail with ``fault``."""
        self._faults[operation].append(_Fault(fault, code or fault.value, known))

    def report_fills_twice(self, enabled: bool = True) -> None:
        """Report every fill twice from :meth:`fills_since` (duplicate event delivery)."""
        self._duplicate_fills = enabled

    def report_unexpected_fill(self, fill: BrokerFill) -> None:
        """Report a fill that matches no order of this broker (for ORD-ERR-4 tests)."""
        self._fills.append(fill)

    # ------------------------------------------------------------------ BrokerPort

    @property
    def environment(self) -> BrokerEnvironment:
        return BrokerEnvironment.SIM

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            fractional_orders=True,
            fractional_step=self._fees.fractional_quantity_step,
            min_fractional_buy_usd=self._fees.min_fractional_buy_usd,
            order_types=frozenset({OrderType.LIMIT, OrderType.MARKET}),
        )

    def account(self) -> AccountSnapshot:
        return AccountSnapshot(
            account_id=self._account_id,
            environment=self.environment,
            ts=self._clock.now(),
            cash_usd_settled=self._ledger.cash_usd_settled,
            cash_usd_unsettled=self._ledger.cash_usd_unsettled,
            cash_jpy=self._ledger.cash_jpy,
        )

    def positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(symbol=p.symbol, quantity=p.quantity, cost_usd=p.cost_usd)
            for p in sorted(self._ledger.positions.values(), key=lambda p: p.symbol)
        ]

    def open_orders(self) -> list[BrokerOrder]:
        return [o.snapshot() for o in self._orders.values() if not o.status.is_terminal]

    def submit(self, request: OrderRequest) -> BrokerOrderAck:
        fault = self._take_fault(SimOperation.SUBMIT)
        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return self._ack(existing)
        reason = self._reject_reason(request)
        order = _SimOrder(
            request=request,
            broker_order_id=f"SIM-{len(self._orders) + 1}",
            status=OrderStatus.REJECTED if reason else OrderStatus.SUBMITTED,
            updated_at=self._clock.now(),
            reject_code=reason,
            reserve_price=request.limit_price
            if request.side is Side.BUY and request.limit_price
            else Decimal(0),
        )
        self._orders[request.client_order_id] = order
        if reason is not None:
            raise OrderRejectedError(reason, "refused by the simulator")
        if fault is not None and fault.fault is SimFault.TIMEOUT_AFTER_ACCEPT:
            raise BrokerUnavailableError("timeout after the order was accepted")
        return self._ack(order)

    def cancel(self, client_order_id: str) -> None:
        fault = self._take_fault(SimOperation.CANCEL)
        order = self._order(client_order_id)
        if not order.status.is_terminal:
            order.status = OrderStatus.CANCELLED
            order.updated_at = self._clock.now()
        if fault is not None and fault.fault is SimFault.TIMEOUT_AFTER_ACCEPT:
            raise BrokerUnavailableError("timeout after the cancel was processed")

    def order_status(self, client_order_id: str) -> BrokerOrder:
        self._take_fault(SimOperation.STATUS)
        return self._order(client_order_id).snapshot()

    def fills_since(self, ts: datetime) -> list[BrokerFill]:
        fills = [f for f in self._fills if f.fill.ts >= ts]
        if self._duplicate_fills:
            return [f for f in fills for _ in range(2)]
        return fills

    # ------------------------------------------------------------------ market simulation

    def on_quote(
        self, quote: Quote, *, max_fill_quantity: Decimal | None = None
    ) -> list[BrokerFill]:
        """Fill open orders in ``quote.symbol`` that the quote makes executable."""
        fills = []
        for order in list(self._orders.values()):
            if order.status.is_terminal or order.request.symbol != quote.symbol:
                continue
            price = self._fill_price(order, quote)
            if price is None:
                continue
            quantity = order.remaining
            if max_fill_quantity is not None:
                quantity = min(quantity, max_fill_quantity)
            fills.append(self._execute(order, quantity, price))
        return fills

    def expire_day_orders(self) -> list[str]:
        """End of session: open DAY orders expire. Returns their client order IDs."""
        expired = []
        for order in self._orders.values():
            if not order.status.is_terminal:
                order.status = OrderStatus.EXPIRED
                order.updated_at = self._clock.now()
                expired.append(order.request.client_order_id)
        return expired

    def settle(self, as_of: date) -> None:
        self._ledger.settle(as_of, self._clock.now())

    # ------------------------------------------------------------------ internals

    def _take_fault(self, operation: SimOperation) -> _Fault | None:
        queue = self._faults[operation]
        if not queue:
            return None
        fault = queue.popleft()
        if fault.fault is SimFault.TIMEOUT_BEFORE_ACCEPT:
            raise BrokerUnavailableError("timeout; the request did not reach the broker")
        if fault.fault is SimFault.REJECT:
            raise OrderRejectedError(fault.code, "injected rejection", known=fault.known)
        if fault.fault is SimFault.RATE_LIMIT:
            raise RateLimitedError("too many requests")
        if fault.fault is SimFault.AUTH_FAILURE:
            raise BrokerAuthError("401 unauthorized")
        return fault

    def _order(self, client_order_id: str) -> _SimOrder:
        order = self._orders.get(client_order_id)
        if order is None:
            raise OrderNotFoundError(client_order_id)
        return order

    @staticmethod
    def _ack(order: _SimOrder) -> BrokerOrderAck:
        return BrokerOrderAck(
            client_order_id=order.request.client_order_id,
            broker_order_id=order.broker_order_id,
            status=order.status,
        )

    def _reject_reason(self, request: OrderRequest) -> str | None:
        if request.extended_hours:
            return "EXTENDED_HOURS_NOT_ALLOWED"
        if request.order_type not in self.capabilities().order_types:
            return "ORDER_TYPE_NOT_SUPPORTED"
        if request.quantity % self._fees.fractional_quantity_step != 0:
            return "INVALID_QUANTITY_STEP"
        if request.side is Side.SELL:
            held = self._ledger.position(request.symbol)
            selling = sum(
                (
                    o.remaining
                    for o in self._orders.values()
                    if not o.status.is_terminal
                    and o.request.side is Side.SELL
                    and o.request.symbol == request.symbol
                ),
                Decimal(0),
            )
            available = (held.quantity if held else Decimal(0)) - selling
            return "INSUFFICIENT_POSITION" if request.quantity > available else None
        if request.limit_price is None:
            return "MARKET_BUY_NOT_SUPPORTED"
        notional = request.quantity * request.limit_price
        if request.quantity % 1 != 0 and notional < self._fees.min_fractional_buy_usd:
            return "BELOW_MIN_AMOUNT"
        needed = notional + total_order_fees(
            self._fees, Side.BUY, request.quantity, request.limit_price
        )
        if needed > self._ledger.cash_usd_settled - self._reserved_usd():
            return "INSUFFICIENT_BUYING_POWER"
        return None

    def _reserved_usd(self) -> Decimal:
        reserved = Decimal(0)
        for order in self._orders.values():
            if order.status.is_terminal or order.request.side is not Side.BUY:
                continue
            reserved += order.remaining * order.reserve_price + total_order_fees(
                self._fees, Side.BUY, order.remaining, order.reserve_price
            )
        return reserved

    def _fill_price(self, order: _SimOrder, quote: Quote) -> Decimal | None:
        request = order.request
        if request.side is Side.BUY:  # always a limit order (market buys are refused)
            if quote.ask > order.reserve_price:
                return None
            price = (quote.ask * (1 + self._slippage)).quantize(CENT, rounding=ROUND_CEILING)
            return min(price, order.reserve_price)
        price = (quote.bid * (1 - self._slippage)).quantize(CENT, rounding=ROUND_FLOOR)
        if request.limit_price is not None:
            if quote.bid < request.limit_price:
                return None
            price = max(price, request.limit_price)
        return price

    def _execute(self, order: _SimOrder, quantity: Decimal, price: Decimal) -> BrokerFill:
        request = order.request
        now = self._clock.now()
        order.fill_count += 1
        fill = Fill(
            fill_id=f"{order.broker_order_id}-F{order.fill_count}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=quantity,
            price=price,
            ts=now,
            source="sim",
        )
        costs = tuple(
            TradeCost(
                ts=now,
                type=kind,
                amount=Money.of(amount, Currency.USD),
                client_order_id=request.client_order_id,
                estimated=True,
                source=f"sim:{self._fees.name}",
            )
            for kind, amount in order_fees(self._fees, request.side, quantity, price)
        )
        settles_on = self._settlement_date(now.date()) if request.side is Side.SELL else None
        self._ledger.record_fill(fill, costs, settles_on=settles_on)
        order.filled_quantity += quantity
        order.fill_value += quantity * price
        order.status = OrderStatus.FILLED if order.remaining == 0 else OrderStatus.PARTIALLY_FILLED
        order.updated_at = now
        broker_fill = BrokerFill(fill=fill, costs=costs, settles_on=settles_on)
        self._fills.append(broker_fill)
        return broker_fill
