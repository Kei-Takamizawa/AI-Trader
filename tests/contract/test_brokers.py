"""The same externally observable BrokerPort contract for both implementations."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aitrader.broker.port import (
    BrokerAuthError,
    BrokerPort,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitedError,
)
from aitrader.broker.sim import SimBroker, SimFault, SimOperation
from aitrader.broker.webull import SandboxCredentials, WebullBroker, WebullSandboxSettings
from aitrader.clock import SimClock
from aitrader.config import ConfigError, LiveDisabledError
from aitrader.domain import BrokerEnvironment, Currency, Money, OrderStatus
from tests.contract.webull_fake import ENV, NOW, FakeTransport
from tests.unit.broker.test_sim_broker import order
from tests.unit.execution.builders import quote


@dataclass
class Harness:
    broker: BrokerPort
    clock: SimClock
    fake: FakeTransport | None

    def fault(self, fault: SimFault, *, known: bool = True) -> None:
        if isinstance(self.broker, SimBroker):
            self.broker.inject(
                SimOperation.SUBMIT,
                fault,
                code="INVALID_PARAMETER" if known else "NEW_TEST_CODE",
                known=known,
            )
        else:
            assert self.fake is not None
            if fault is SimFault.TIMEOUT_AFTER_ACCEPT:
                self.fake.timeout_accept = True
            else:
                status = {
                    SimFault.REJECT: 417,
                    SimFault.AUTH_FAILURE: 401,
                    SimFault.RATE_LIMIT: 429,
                }[fault]
                self.fake.queue(
                    "place",
                    {
                        "error_code": "INVALID_PARAMETER" if known else "NEW_TEST_CODE",
                        "message": "test rejection",
                    },
                    status,
                )

    def fill(self, final: bool) -> None:
        if isinstance(self.broker, SimBroker):
            self.broker.on_quote(
                quote(bid="99", ask="100"), max_fill_quantity=None if final else Decimal("0.5")
            )
        else:
            assert self.fake is not None
            self.fake.orders["order-1"].update(
                status="FILLED" if final else "PARTIAL_FILLED",
                filled_quantity="1" if final else "0.5",
                filled_price="100",
                filled_time_at=self.clock.now().isoformat(),
            )


@pytest.fixture(params=["sim", "webull"])
def harness(request: pytest.FixtureRequest) -> Harness:
    clock = SimClock(NOW)
    if request.param == "sim":
        sim = SimBroker(clock)
        sim.fund(Money.of("1000", Currency.USD))
        sim.fund(Money.of("200", Currency.JPY))
        return Harness(sim, clock, None)
    fake = FakeTransport(clock)
    fake.fractional_accept = True
    return Harness(
        WebullBroker(
            clock,
            settlement_date=lambda day: day + timedelta(days=1),
            credentials=SandboxCredentials(ENV),
            transport=fake,
        ),
        clock,
        fake,
    )


def test_submit_status_resubmit_and_cancel(harness: Harness) -> None:
    broker = harness.broker
    assert isinstance(broker, BrokerPort)
    ack = broker.submit(order("order-1", quantity="1", limit="100"))
    assert ack.status is OrderStatus.SUBMITTED
    assert broker.order_status("order-1").quantity == 1
    harness.clock.advance(timedelta(seconds=2))
    assert broker.submit(order("order-1", quantity="1", limit="100")) == ack
    assert [item.client_order_id for item in broker.open_orders()] == ["order-1"]
    broker.cancel("order-1")
    assert broker.order_status("order-1").status is OrderStatus.CANCELLED
    if harness.fake:
        assert sum(op == "place" for op, _ in harness.fake.calls) == 1


def test_partial_and_final_fills(harness: Harness) -> None:
    harness.broker.submit(order("order-1", quantity="1", limit="100"))
    harness.fill(False)
    partial = harness.broker.order_status("order-1")
    if harness.fake is None:
        assert partial.status is OrderStatus.PARTIALLY_FILLED
        assert partial.filled_quantity == Decimal("0.5")
    else:
        assert partial.status is OrderStatus.SUBMITTED
        assert partial.filled_quantity == 0
        assert partial.average_fill_price is None
    first = harness.broker.fills_since(NOW)
    seen = {item.fill.fill_id: item.fill for item in first}
    assert sum((fill.quantity for fill in seen.values()), Decimal(0)) <= Decimal("0.5")
    harness.clock.advance(timedelta(seconds=2))
    harness.fill(True)
    final = harness.broker.order_status("order-1")
    assert final.status is OrderStatus.FILLED
    assert final.filled_quantity == 1
    assert final.average_fill_price is not None
    for _ in range(2):
        harness.clock.advance(timedelta(seconds=2))
        seen.update({item.fill.fill_id: item.fill for item in harness.broker.fills_since(NOW)})
        assert sum((fill.quantity for fill in seen.values()), Decimal(0)) == final.filled_quantity
        assert final.filled_quantity <= final.quantity


@pytest.mark.parametrize("known", [True, False])
def test_known_and_unknown_rejections(harness: Harness, known: bool) -> None:
    harness.fault(SimFault.REJECT, known=known)
    with pytest.raises(OrderRejectedError) as caught:
        harness.broker.submit(order(quantity="1", limit="100"))
    assert caught.value.known is known
    assert caught.value.code == ("INVALID_PARAMETER" if known else "NEW_TEST_CODE")


def test_unknown_submit_outcome_then_lookup(harness: Harness) -> None:
    harness.fault(SimFault.TIMEOUT_AFTER_ACCEPT)
    with pytest.raises(BrokerUnavailableError):
        harness.broker.submit(order("order-1", quantity="1", limit="100"))
    assert harness.broker.order_status("order-1").status is OrderStatus.SUBMITTED
    if harness.fake:
        assert sum(op == "place" for op, _ in harness.fake.calls) == 1


@pytest.mark.parametrize(
    ("fault", "error"),
    [
        (SimFault.AUTH_FAILURE, BrokerAuthError),
        (SimFault.RATE_LIMIT, RateLimitedError),
    ],
)
def test_auth_and_rate_errors(harness: Harness, fault: SimFault, error: type[Exception]) -> None:
    harness.fault(fault)
    with pytest.raises(error):
        harness.broker.submit(order(quantity="1", limit="100"))


@pytest.mark.parametrize("quantity", ["0.09999", "0.10000", "0.10001", "0.100001"])
def test_fractional_step(harness: Harness, quantity: str) -> None:
    request = order(quantity=quantity, limit="100")
    if quantity == "0.100001":
        with pytest.raises(OrderRejectedError):
            harness.broker.submit(request)
    else:
        assert harness.broker.submit(request).status is OrderStatus.SUBMITTED
    assert harness.broker.capabilities().fractional_step == Decimal("0.00001")


@pytest.mark.parametrize("cid", ["", "a" * 33, "bad space", "a\n"])
def test_invalid_client_id(harness: Harness, cid: str) -> None:
    with pytest.raises(ValidationError):
        harness.broker.submit(order(cid))


def test_account_and_position_mapping(harness: Harness) -> None:
    account = harness.broker.account()
    assert account.cash_usd_settled == 1000
    assert account.cash_usd_unsettled == 0
    assert account.cash_jpy == 200
    harness.broker.submit(order("order-1", quantity="1", limit="100"))
    harness.fill(True)
    positions = harness.broker.positions()
    assert len(positions) == 1
    assert positions[0].quantity == 1
    assert positions[0].cost_usd == 100


def test_unknown_order(harness: Harness) -> None:
    with pytest.raises(OrderNotFoundError):
        harness.broker.order_status("unknown")


def test_production_host_refused(harness: Harness) -> None:
    # The common suite also verifies routing cannot opt a broker into production.
    if isinstance(harness.broker, SimBroker):
        assert harness.broker.environment is BrokerEnvironment.SIM
    with pytest.raises(ConfigError):
        WebullBroker(
            harness.clock,
            settlement_date=lambda day: day + timedelta(days=1),
            settings=WebullSandboxSettings(endpoint="api.webull.co.jp"),
        )
    with pytest.raises(LiveDisabledError):
        WebullBroker(
            harness.clock,
            settlement_date=lambda day: day + timedelta(days=1),
            environment=BrokerEnvironment.WEBULL_LIVE,
        )
