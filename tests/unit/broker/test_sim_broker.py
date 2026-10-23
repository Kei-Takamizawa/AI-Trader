"""SimBroker: order acceptance, fills from quotes, fees, settlement and fault injection."""

from datetime import date, timedelta
from typing import Any

import pytest

from aitrader.broker.port import (
    BrokerAuthError,
    BrokerFill,
    BrokerPort,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitedError,
)
from aitrader.broker.sim import SimBroker, SimFault, SimOperation, next_weekday
from aitrader.clock import SimClock
from aitrader.domain import (
    BrokerEnvironment,
    Currency,
    Fill,
    IntentPurpose,
    Money,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    TradeCostType,
)
from tests.unit.execution.builders import NOW, D, quote

STEP = D("0.00001")


def broker(cash: str = "300") -> tuple[SimBroker, SimClock]:
    clock = SimClock(NOW)
    sim = SimBroker(clock)
    sim.fund(Money.of(cash, Currency.USD))
    return sim, clock


def order(
    cid: str = "ORDER-1",
    side: Side = Side.BUY,
    quantity: str = "0.3",
    limit: str | None = "100.05",
    **kw: object,
) -> OrderRequest:
    fields: dict[str, object] = {
        "client_order_id": cid,
        "intent_id": f"intent-{cid}",
        "symbol": "SPY",
        "side": side,
        "purpose": IntentPurpose.ENTRY if side is Side.BUY else IntentPurpose.EXIT,
        "order_type": OrderType.LIMIT if limit else OrderType.MARKET,
        "quantity": quantity,
        "limit_price": limit,
    }
    fields.update(kw)
    return OrderRequest.model_validate(fields)


def test_sim_broker_implements_the_port() -> None:
    sim, _ = broker()
    assert isinstance(sim, BrokerPort)
    assert sim.environment is BrokerEnvironment.SIM
    capabilities = sim.capabilities()
    assert capabilities.fractional_orders
    assert capabilities.fractional_step == STEP
    assert capabilities.min_fractional_buy_usd == 1
    assert sim.account().cash_usd_settled == D(300)


def test_buy_fills_at_the_ask_plus_slippage_within_the_limit() -> None:
    sim, _ = broker()
    ack = sim.submit(order(limit="100.10"))
    assert ack.status is OrderStatus.SUBMITTED
    assert [o.client_order_id for o in sim.open_orders()] == ["ORDER-1"]
    fills = sim.on_quote(quote(ask="100.01"))
    assert len(fills) == 1
    fill = fills[0].fill
    assert fill.price == D("100.07")  # 100.01 * 1.0005 = 100.060005, rounded up to the cent
    status = sim.order_status("ORDER-1")
    assert status.status is OrderStatus.FILLED
    assert status.average_fill_price == fill.price
    assert sim.open_orders() == []
    assert [p.symbol for p in sim.positions()] == ["SPY"]
    assert [c.type for c in fills[0].costs] == [TradeCostType.CAT_FEE]
    assert fills[0].settles_on is None


def test_buy_never_fills_above_its_limit() -> None:
    sim, _ = broker()
    sim.submit(order(limit="100.00"))
    assert sim.on_quote(quote(bid="100.00", ask="100.02")) == []
    fills = sim.on_quote(quote(bid="99.98", ask="100.00"))
    assert fills[0].fill.price == D("100.00")  # 100.05 with slippage, capped at the limit


def test_sell_fills_at_the_bid_minus_slippage_and_settles_next_weekday() -> None:
    sim, clock = broker()
    sim.submit(order())
    sim.on_quote(quote())
    sim.submit(order("ORDER-2", Side.SELL, "0.3", "99.00"))
    assert sim.on_quote(quote(bid="98.99", ask="99.01")) == []
    fills = sim.on_quote(quote(bid="99.99", ask="100.01"))
    sale = fills[0]
    assert sale.fill.price == D("99.94")  # 99.99 * 0.9995 = 99.940005, rounded down
    assert {c.type for c in sale.costs} == {
        TradeCostType.CAT_FEE,
        TradeCostType.FINRA_FEE,
        TradeCostType.SEC_FEE,
    }
    assert sale.settles_on == date(2026, 10, 23)
    assert sim.account().cash_usd_unsettled > 0
    clock.set(NOW + timedelta(days=1))
    sim.settle(date(2026, 10, 23))
    assert sim.account().cash_usd_unsettled == 0


def test_market_sell_fills_at_the_bid_minus_slippage() -> None:
    sim, _ = broker()
    sim.submit(order())
    sim.on_quote(quote())
    sim.submit(order("ORDER-2", Side.SELL, "0.3", None, purpose=IntentPurpose.STOP_EXIT))
    fills = sim.on_quote(quote(bid="90.00", ask="90.02"))
    assert fills[0].fill.price == D("89.95")


def test_market_buy_fills_at_the_ask_when_accepted() -> None:
    sim, _ = broker()
    with pytest.raises(OrderRejectedError, match="MARKET_BUY_NOT_SUPPORTED"):
        sim.submit(order(limit=None))
    assert sim.order_status("ORDER-1").status is OrderStatus.REJECTED


def test_partial_fills() -> None:
    sim, _ = broker()
    sim.submit(order())
    sim.on_quote(quote(), max_fill_quantity=D("0.1"))
    assert sim.order_status("ORDER-1").status is OrderStatus.PARTIALLY_FILLED
    sim.on_quote(quote(), max_fill_quantity=D("0.1"))
    sim.on_quote(quote(), max_fill_quantity=D("0.5"))
    status = sim.order_status("ORDER-1")
    assert status.status is OrderStatus.FILLED
    assert status.filled_quantity == D("0.3")
    assert len(sim.fills_since(NOW)) == 3


def test_quotes_for_other_symbols_do_not_fill() -> None:
    sim, _ = broker()
    sim.submit(order())
    assert sim.on_quote(quote(symbol="QQQ")) == []


@pytest.mark.parametrize(
    ("request_kwargs", "code"),
    [
        ({"extended_hours": True}, "EXTENDED_HOURS_NOT_ALLOWED"),
        ({"order_type": OrderType.STOP, "limit_price": None}, "ORDER_TYPE_NOT_SUPPORTED"),
        ({"quantity": "0.000001"}, "INVALID_QUANTITY_STEP"),
        ({"quantity": "0.009"}, "BELOW_MIN_AMOUNT"),
        ({"quantity": "3"}, "INSUFFICIENT_BUYING_POWER"),
    ],
)
def test_business_rejections(request_kwargs: dict[str, Any], code: str) -> None:
    sim, _ = broker()
    with pytest.raises(OrderRejectedError, match=code) as raised:
        sim.submit(order(**request_kwargs))
    assert raised.value.known
    assert raised.value.code == code


def test_open_buys_reserve_buying_power() -> None:
    sim, _ = broker("50")
    sim.submit(order(quantity="0.3"))
    with pytest.raises(OrderRejectedError, match="INSUFFICIENT_BUYING_POWER"):
        sim.submit(order("ORDER-2", quantity="0.3"))


def test_sells_cannot_exceed_the_position_not_already_being_sold() -> None:
    sim, _ = broker()
    sim.submit(order())
    sim.on_quote(quote())
    sim.submit(order("ORDER-2", Side.SELL, "0.2", "200"))
    with pytest.raises(OrderRejectedError, match="INSUFFICIENT_POSITION"):
        sim.submit(order("ORDER-3", Side.SELL, "0.2", "200"))
    with pytest.raises(OrderRejectedError, match="INSUFFICIENT_POSITION"):
        sim.submit(order("ORDER-4", Side.SELL, "1", "200", symbol="QQQ"))


def test_submit_is_idempotent_on_client_order_id() -> None:
    sim, _ = broker()
    first = sim.submit(order())
    assert sim.submit(order()) == first
    assert len(sim.open_orders()) == 1


def test_cancel_and_expire() -> None:
    sim, _ = broker()
    sim.submit(order())
    sim.submit(order("ORDER-2", limit="90"))
    sim.cancel("ORDER-1")
    assert sim.order_status("ORDER-1").status is OrderStatus.CANCELLED
    sim.cancel("ORDER-1")  # cancelling a closed order changes nothing
    assert sim.expire_day_orders() == ["ORDER-2"]
    assert sim.order_status("ORDER-2").status is OrderStatus.EXPIRED
    with pytest.raises(OrderNotFoundError):
        sim.cancel("missing")
    with pytest.raises(OrderNotFoundError):
        sim.order_status("missing")


def test_next_weekday_skips_weekends() -> None:
    assert next_weekday(date(2026, 10, 22)) == date(2026, 10, 23)  # Thursday -> Friday
    assert next_weekday(date(2026, 10, 23)) == date(2026, 10, 26)  # Friday -> Monday


# ---------------------------------------------------------------- fault injection


def test_timeout_before_accept_creates_no_order() -> None:
    sim, _ = broker()
    sim.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_BEFORE_ACCEPT)
    with pytest.raises(BrokerUnavailableError):
        sim.submit(order())
    with pytest.raises(OrderNotFoundError):
        sim.order_status("ORDER-1")


def test_timeout_after_accept_creates_the_order() -> None:
    sim, _ = broker()
    sim.inject(SimOperation.SUBMIT, SimFault.TIMEOUT_AFTER_ACCEPT)
    with pytest.raises(BrokerUnavailableError):
        sim.submit(order())
    assert sim.order_status("ORDER-1").status is OrderStatus.SUBMITTED


def test_cancel_timeout_after_processing() -> None:
    sim, _ = broker()
    sim.submit(order())
    sim.inject(SimOperation.CANCEL, SimFault.TIMEOUT_AFTER_ACCEPT)
    with pytest.raises(BrokerUnavailableError):
        sim.cancel("ORDER-1")
    assert sim.order_status("ORDER-1").status is OrderStatus.CANCELLED


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        (SimFault.REJECT, OrderRejectedError),
        (SimFault.RATE_LIMIT, RateLimitedError),
        (SimFault.AUTH_FAILURE, BrokerAuthError),
        (SimFault.TIMEOUT_BEFORE_ACCEPT, BrokerUnavailableError),
    ],
)
def test_injected_faults(fault: SimFault, error: type[Exception]) -> None:
    sim, _ = broker()
    sim.inject(SimOperation.STATUS, fault, code="X1", known=False)
    with pytest.raises(error):
        sim.order_status("ORDER-1")
    with pytest.raises(OrderNotFoundError):  # the fault is used up
        sim.order_status("ORDER-1")


def test_injected_rejection_carries_its_code() -> None:
    sim, _ = broker()
    sim.inject(SimOperation.SUBMIT, SimFault.REJECT, code="PLACE_FAILED", known=False)
    with pytest.raises(OrderRejectedError) as raised:
        sim.submit(order())
    assert raised.value.code == "PLACE_FAILED"
    assert not raised.value.known
    assert raised.value.message == "injected rejection"


def test_duplicate_and_unexpected_fill_reports() -> None:
    sim, _ = broker()
    sim.submit(order())
    sim.on_quote(quote())
    sim.report_fills_twice()
    assert len(sim.fills_since(NOW)) == 2
    sim.report_fills_twice(False)
    stray = BrokerFill(
        fill=Fill(
            fill_id="STRAY-1",
            client_order_id="NOBODY-1",
            symbol="SPY",
            side=Side.BUY,
            quantity=D(1),
            price=D(100),
            ts=NOW,
            source="sim",
        )
    )
    sim.report_unexpected_fill(stray)
    assert sim.fills_since(NOW)[-1] == stray
    assert sim.fills_since(NOW + timedelta(seconds=1)) == []
    assert sim.ledger.position("SPY") is not None


def test_rejection_error_message() -> None:
    assert str(OrderRejectedError("CODE")) == "CODE"
    assert str(OrderRejectedError("CODE", "why")) == "CODE: why"
