"""Strict response parsing avoids inventing cash or execution data on malformed input."""

import logging
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from aitrader.broker.port import BrokerFill, BrokerOrder, BrokerPosition, BrokerUnavailableError
from aitrader.domain import Currency, Fill, Money, OrderStatus, OrderType, Side, TradeCost
from aitrader.domain.enums import TradeCostType
from aitrader.domain.types import require_utc

ORDER_FIELDS = (
    "client_order_id",
    "order_id",
    "symbol",
    "side",
    "status",
    "order_type",
    "time_in_force",
    "total_quantity",
    "place_time_at",
    "account_tax_type",
)
BALANCE_FIELDS = (
    "currency",
    "cash_balance",
    "market_value",
    "buying_power",
    "unrealized_profit_loss",
)
POSITION_FIELDS = (
    "currency",
    "quantity",
    "available_quantity",
    "market_value",
    "symbol",
    "instrument_id",
    "instrument_type",
    "last_price",
    "cost_price",
    "unrealized_profit_loss",
    "base_currency",
    "fx_rate",
    "base_currency_market_value",
)
STATUSES = {
    "PENDING": OrderStatus.SUBMITTED,
    "SUBMITTED": OrderStatus.SUBMITTED,
    "PARTIAL_FILLED": OrderStatus.SUBMITTED,
    "FILLED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "FAILED": OrderStatus.REJECTED,
}


def present(value: Any) -> bool:
    return value is not None and value != ""


def obj(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BrokerUnavailableError(field)
    return value


def array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise BrokerUnavailableError(field)
    return value


def text(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise BrokerUnavailableError(field)
    return value


def number(data: dict[str, Any], field: str, *, optional: bool = False) -> Decimal:
    value = data.get(field)
    if optional and not present(value):
        return Decimal(0)
    try:
        if not isinstance(value, str):
            raise ValueError
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError
        return result
    except ValueError, InvalidOperation:
        raise BrokerUnavailableError(field) from None


def timestamp(data: dict[str, Any], field: str) -> datetime:
    try:
        return require_utc(datetime.fromisoformat(text(data, field)))
    except ValueError:
        raise BrokerUnavailableError(field) from None


def _side(data: dict[str, Any]) -> Side:
    try:
        return Side(text(data, "side"))
    except ValueError:
        raise BrokerUnavailableError("side") from None


def _order_type(data: dict[str, Any]) -> OrderType:
    try:
        return OrderType(text(data, "order_type"))
    except ValueError:
        raise BrokerUnavailableError("order_type") from None


def order(data: dict[str, Any]) -> BrokerOrder:
    for field in ORDER_FIELDS:
        text(data, field)
    timestamp(data, "place_time_at")
    status = text(data, "status")
    if status not in STATUSES:
        logging.getLogger(__name__).warning("Unknown Webull order status: %s", status)
    filled = number(data, "filled_quantity", optional=True)
    price = number(data, "filled_price", optional=True)
    try:
        snapshot = BrokerOrder(
            client_order_id=text(data, "client_order_id"),
            broker_order_id=text(data, "order_id"),
            symbol=text(data, "symbol"),
            side=_side(data),
            order_type=_order_type(data),
            quantity=number(data, "total_quantity"),
            limit_price=number(data, "limit_price") if present(data.get("limit_price")) else None,
            status=STATUSES.get(status, OrderStatus.UNKNOWN),
            filled_quantity=filled,
            average_fill_price=price if filled > 0 and price > 0 else None,
            updated_at=timestamp(
                data, "filled_time_at" if present(data.get("filled_time_at")) else "place_time_at"
            ),
        )
    except ValidationError as exc:
        raise BrokerUnavailableError(str(exc.errors()[0]["loc"][0])) from None
    # A working cumulative execution cannot be booked until its final snapshot arrives.
    if status in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}:
        return snapshot.model_copy(
            update={"filled_quantity": Decimal(0), "average_fill_price": None}
        )
    return snapshot


def positions(body: Any) -> list[BrokerPosition]:
    result = []
    for raw in array(body, "positions"):
        data = obj(raw, "position")
        for field in POSITION_FIELDS:
            text(data, field)
        for field in (
            "quantity",
            "available_quantity",
            "market_value",
            "last_price",
            "cost_price",
            "unrealized_profit_loss",
            "fx_rate",
            "base_currency_market_value",
        ):
            number(data, field)
        if data["instrument_type"] != "EQUITY" or data["currency"] != "USD":
            continue
        quantity = number(data, "quantity")
        if quantity == 0:
            continue
        try:
            result.append(
                BrokerPosition(
                    symbol=text(data, "symbol"),
                    quantity=quantity,
                    cost_usd=number(data, "cost_price") * quantity,
                )
            )
        except ValidationError as exc:
            raise BrokerUnavailableError(str(exc.errors()[0]["loc"][0])) from None
    return result


def fill(
    data: dict[str, Any],
    snapshot: BrokerOrder,
    settlement_date: Callable[[date], date],
    observed_at: datetime,
) -> BrokerFill:
    """Book at observation time; preserve the execution's New York date for settlement."""
    price = snapshot.average_fill_price
    if price is None:
        raise BrokerUnavailableError("filled_price")
    costs = []
    commission = data.get("commission")
    raw_fees = data.get("fees")
    entries = array(raw_fees, "fees") if present(raw_fees) else []
    if present(commission):
        entries = [
            *entries,
            {
                "type": "COMMISSION",
                "actual_value": obj(commission, "commission").get("actual_commission"),
            },
        ]
    for raw in entries:
        entry = obj(raw, "fees")
        if not present(entry.get("actual_value")):
            continue
        try:
            kind = TradeCostType(text(entry, "type"))
        except ValueError:
            kind = TradeCostType.OTHER
        try:
            costs.append(
                TradeCost(
                    ts=observed_at,
                    type=kind,
                    amount=Money.of(number(entry, "actual_value"), Currency.USD),
                    client_order_id=snapshot.client_order_id,
                    estimated=False,
                    source="order-detail",
                )
            )
        except ValidationError:
            raise BrokerUnavailableError("actual_value") from None
    return BrokerFill(
        fill=Fill(
            fill_id=f"{snapshot.client_order_id}:final",
            client_order_id=snapshot.client_order_id,
            symbol=snapshot.symbol,
            side=snapshot.side,
            quantity=snapshot.filled_quantity,
            price=price,
            ts=observed_at,
            source="order-detail",
        ),
        costs=tuple(costs),
        settles_on=(
            settlement_date(snapshot.updated_at.astimezone(ZoneInfo("America/New_York")).date())
            if snapshot.side is Side.SELL
            else None
        ),
    )
