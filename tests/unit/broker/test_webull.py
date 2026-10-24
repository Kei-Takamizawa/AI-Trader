"""Sandbox boundary guards, strict parsing and real pinned SDK request construction."""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aitrader.broker.port import (
    BrokerAuthError,
    BrokerError,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitedError,
)
from aitrader.broker.webull import (
    SandboxCredentials,
    WebullBroker,
    WebullSandboxSettings,
    parsing,
    sdk,
)
from aitrader.broker.webull.broker import QueryLimiter, shape, statuses
from aitrader.broker.webull.settings import SANDBOX_HOST
from aitrader.clock import SimClock
from aitrader.config import ConfigError, LiveDisabledError
from aitrader.config.loader import load_webull_sandbox_settings
from aitrader.domain import BrokerEnvironment, OrderStatus, OrderType, TimeInForce
from tests.contract.webull_fake import (
    ENV,
    NOW,
    FakeResponse,
    FakeTransport,
    balance,
    order_item,
    position,
)
from tests.unit.broker.test_sim_broker import order


def setup() -> tuple[WebullBroker, FakeTransport]:
    clock = SimClock(NOW)
    fake = FakeTransport(clock)
    return WebullBroker(clock, credentials=SandboxCredentials(ENV), transport=fake), fake


@pytest.mark.parametrize(
    "host",
    [
        "api.webull.co.jp",
        "events-api.webull.co.jp",
        "data-api.webull.co.jp",
        "jp-openapi-events.uat.webullbroker.com",
        "data-api.uat.webullbroker.com",
        f"https://{SANDBOX_HOST}",
        f"{SANDBOX_HOST}:443",
        f"{SANDBOX_HOST}/",
        f"user@{SANDBOX_HOST}",
        f"{SANDBOX_HOST}.",
        f" {SANDBOX_HOST}",
        "",
        "evil.test",
    ],
)
def test_host_rejected_before_client_build(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("SDK client must not be built")

    monkeypatch.setattr(sdk, "ApiClient", forbidden)
    with pytest.raises(ConfigError, match="bare UAT"):
        WebullBroker(SimClock(NOW), settings=WebullSandboxSettings(endpoint=host))


@pytest.mark.parametrize("environment", [BrokerEnvironment.SIM, BrokerEnvironment.WEBULL_LIVE])
def test_non_sandbox_refused(environment: BrokerEnvironment) -> None:
    with pytest.raises(LiveDisabledError):
        WebullBroker(SimClock(NOW), environment=environment)


def test_all_sdk_production_hosts_refused() -> None:
    # Read only the installed public endpoint definitions, never token or configuration files.
    import importlib.util

    spec = importlib.util.find_spec("webull.core")
    assert spec is not None
    assert spec.origin is not None
    endpoints = json.loads((Path(spec.origin).parent / "data/endpoints.json").read_text())

    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for entry in value.values() for item in strings(entry)]
        if isinstance(value, list):
            return [item for entry in value for item in strings(entry)]
        return []

    hosts = [value for value in strings(endpoints) if "." in value]
    assert hosts
    for host in hosts:
        with pytest.raises(ConfigError):
            WebullBroker(SimClock(NOW), settings=WebullSandboxSettings(endpoint=host))


def test_sdk_resolved_production_host_refused_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = sdk.ApiClient

    def broken(*args: Any, **kwargs: Any) -> Any:
        client = real_client(*args, **kwargs)
        client.add_endpoint = lambda *args: None
        return client

    monkeypatch.setattr(sdk, "ApiClient", broken)
    with pytest.raises(ConfigError, match="resolved trading host"):
        sdk.SDKTransport(SandboxCredentials(ENV), SANDBOX_HOST)


def test_real_sdk_client_override_pin_no_retries_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = sdk.SDKTransport(SandboxCredentials(ENV), SANDBOX_HOST.upper())
    token_dir = Path(transport._client.get_token_dir())
    assert token_dir.is_dir()
    assert token_dir.stat().st_mode & 0o777 == 0o700
    assert not token_dir.is_relative_to(Path.cwd())
    assert transport._client._auto_retry is False
    seen = []

    def response(request: Any) -> FakeResponse:
        seen.append(request)
        assert request.get_endpoint() == SANDBOX_HOST
        return FakeResponse({})

    monkeypatch.setattr(transport._client, "get_response", response)
    transport.send(sdk.build_request("balance", "invented-account", {}))
    assert len(seen) == 1
    transport._client.add_endpoint("jp", "api.webull.co.jp")
    with pytest.raises(ConfigError):
        transport.send(sdk.build_request("balance", "invented-account", {}))
    assert len(seen) == 1
    transport.close()
    assert not token_dir.exists()


def test_real_sdk_http_path_logging_errors_and_single_timeout(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    broker = WebullBroker(SimClock(NOW), credentials=SandboxCredentials(ENV))
    assert isinstance(broker._transport, sdk.SDKTransport)
    client = broker._transport._client
    calls = []
    status = 200

    def single(*args: Any) -> Any:
        calls.append(args)
        if status == 0:
            raise TimeoutError(" ".join(ENV.values()))
        body = {"client_order_id": "ORDER-1", "order_id": "broker-1"}
        if status != 200:
            body = {"error_code": "INVALID_PARAMETER", "message": " ".join(ENV.values())}
        encoded = json.dumps(body).encode()
        exception = client._get_server_exception(args[1], status, {}, encoded, SANDBOX_HOST, "")
        return (
            status,
            {"x-app-key": ENV["WEBULL_SANDBOX_APP_KEY"]},
            encoded,
            exception,
            FakeResponse(body, status),
        )

    monkeypatch.setattr(client, "_handle_single_request", single)
    assert broker.place_once(order()).broker_order_id == "broker-1"
    for response_status, error in [
        (401, BrokerAuthError),
        (417, OrderRejectedError),
        (500, BrokerUnavailableError),
        (0, BrokerUnavailableError),
    ]:
        status = response_status
        before = len(calls)
        with pytest.raises(error) as caught:
            broker.place_once(order())
        assert len(calls) == before + 1
        assert all(value not in str(caught.value) for value in ENV.values())
    broker.close()
    assert all(value not in caplog.text for value in ENV.values())


@pytest.mark.parametrize(
    ("status", "op", "error"),
    [
        (401, "balance", BrokerAuthError),
        (403, "positions", BrokerAuthError),
        (429, "detail", RateLimitedError),
        (500, "balance", BrokerUnavailableError),
        (503, "history", BrokerUnavailableError),
        (417, "balance", BrokerUnavailableError),
        (417, "detail", OrderNotFoundError),
        (417, "cancel", OrderRejectedError),
        (400, "accounts", BrokerUnavailableError),
        (302, "accounts", BrokerUnavailableError),
    ],
)
def test_all_http_mappings(status: int, op: str, error: type[BrokerError]) -> None:
    broker, fake = setup()
    fake.queue(op, {"error_code": "INVALID_PARAMETER", "message": "rejected"}, status)
    with pytest.raises(error) as caught:
        broker.call(op)
    assert caught.value.code == "INVALID_PARAMETER"
    assert caught.value.http_status == status


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private"),
        ConnectionError("private"),
        ValueError("private"),
        sdk.ClientException("SDK.HttpError", "private"),
    ],
)
def test_transport_errors_are_sanitised(failure: Exception) -> None:
    broker, fake = setup()
    fake.responses["balance"].append(failure)
    with pytest.raises(BrokerUnavailableError) as caught:
        broker.account()
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    ("cash", "power", "jpy", "settled", "unsettled", "yen"),
    [
        ("100", "60", "200", "60", "40", "200"),
        ("100", "101", "-1", "100", "0", "0"),
        ("100", "-1", "0", "0", "100", "0"),
        ("-1", "5", "0", "0", "0", "0"),
    ],
)
def test_conservative_cash(
    cash: str, power: str, jpy: str, settled: str, unsettled: str, yen: str
) -> None:
    broker, fake = setup()
    fake.queue("balance", balance(cash, power, jpy))
    account = broker.account()
    assert (account.cash_usd_settled, account.cash_usd_unsettled, account.cash_jpy) == tuple(
        Decimal(value) for value in (settled, unsettled, yen)
    )


def test_missing_currencies_and_non_usd_positions() -> None:
    broker, fake = setup()
    fake.queue("balance", {"account_currency_assets": []})
    assert broker.account().cash_usd_settled == 0
    fake.queue(
        "balance",
        {
            "account_currency_assets": [
                {
                    "currency": "EUR",
                    "cash_balance": "1",
                    "buying_power": "1",
                    "market_value": "0",
                    "unrealized_profit_loss": "0",
                }
            ]
        },
    )
    assert broker.account().cash_jpy == 0
    fake.queue(
        "positions",
        [
            position(quantity="0"),
            position(currency="JPY"),
            position(instrument_type="OPTION"),
            position(quantity="0.12345", cost_price="12.34"),
        ],
    )
    assert broker.positions()[0].cost_usd == Decimal("1.5233730")


@pytest.mark.parametrize("status", [*parsing.STATUSES, "NEW_STATUS"])
def test_status_mapping(status: str, caplog: pytest.LogCaptureFixture) -> None:
    snapshot = parsing.order(
        order_item(
            status=status,
            filled_quantity="0.5",
            filled_price="99",
            filled_time_at="2026-10-24T12:00:01.000Z",
        )
    )
    assert snapshot.status is parsing.STATUSES.get(status, OrderStatus.UNKNOWN)
    assert snapshot.filled_quantity == Decimal("0.5")
    assert snapshot.average_fill_price == 99
    assert snapshot.updated_at.second == 1
    if status == "NEW_STATUS":
        assert "NEW_STATUS" in caplog.text
        assert "broker-1" not in caplog.text


@pytest.mark.parametrize("value", [None, "", "0"])
def test_optional_price_absence(value: str | None) -> None:
    snapshot = parsing.order(
        order_item(filled_quantity="1", filled_price=value, limit_price=None, filled_time_at="")
    )
    assert snapshot.average_fill_price is None
    assert snapshot.limit_price is None


@pytest.mark.parametrize("field", list(parsing.ORDER_FIELDS))
def test_required_order_fields_missing(field: str) -> None:
    data = order_item()
    del data[field]
    with pytest.raises(BrokerUnavailableError, match=field):
        parsing.order(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_quantity", "bad"),
        ("total_quantity", "-1"),
        ("total_quantity", "NaN"),
        ("total_quantity", 1),
        ("side", "SHORT"),
        ("order_type", "OTHER"),
        ("place_time_at", "invalid"),
        ("place_time_at", "2026-10-24"),
        ("filled_quantity", "-1"),
        ("limit_price", "-1"),
    ],
)
def test_malformed_order_fields(field: str, value: Any) -> None:
    with pytest.raises(BrokerUnavailableError) as caught:
        parsing.order(order_item(**{field: value}))
    assert str(value) not in str(caught.value)


@pytest.mark.parametrize(
    ("parser", "value", "field"),
    [
        (parsing.obj, [], "body"),
        (parsing.array, {}, "body"),
        (parsing.text, {"body": None}, "body"),
        (parsing.number, {"body": "Infinity"}, "body"),
        (parsing.number, {"body": ""}, "body"),
        (parsing.number, {"body": None}, "body"),
    ],
)
def test_shape_type_errors(parser: Any, value: Any, field: str) -> None:
    with pytest.raises(BrokerUnavailableError, match=field):
        parser(value, field)


def test_position_validation_errors() -> None:
    for data in [position(quantity="-1"), position(symbol="bad symbol")]:
        with pytest.raises(BrokerUnavailableError):
            parsing.positions([data])
    data = position()
    del data["cost_price"]
    with pytest.raises(BrokerUnavailableError, match="cost_price"):
        parsing.positions([data])


def test_all_costs_and_missing_actuals() -> None:
    data = order_item(
        status="FILLED",
        filled_quantity="1",
        filled_price="100",
        commission={"actual_commission": "0.01"},
        fees=[
            {"type": "CAT_FEE", "actual_value": "0.000003"},
            {"type": "NEW_FEE", "actual_value": "0.02"},
            {"type": "SEC_FEE", "receivable_value": "0.03"},
        ],
    )
    execution = parsing.fill(data, parsing.order(data))
    assert [cost.type.value for cost in execution.costs] == ["CAT_FEE", "OTHER", "COMMISSION"]
    assert execution.fill.fill_id == "order-1:1"
    assert all(not cost.estimated for cost in execution.costs)
    data["commission"] = {"receivable_commission": "1"}
    data["fees"] = []
    assert parsing.fill(data, parsing.order(data)).costs == ()
    with pytest.raises(BrokerUnavailableError, match="filled_price"):
        parsing.fill({}, parsing.order(order_item()))
    data["fees"] = [{"type": "CAT_FEE", "actual_value": "-1"}]
    with pytest.raises(BrokerUnavailableError, match="actual_value"):
        parsing.fill(data, parsing.order(data))


def test_pagination_and_fill_deduplication() -> None:
    broker, fake = setup()
    filled = order_item(status="FILLED", filled_quantity="1", filled_price="100")
    for op in ["open", "history"]:
        fake.queue(op, {"data": [{"orders": [filled]}], "pagination_key": "page-2"})
        fake.queue(op, {"data": [{"orders": [filled]}], "pagination_key": ""})
    assert len(broker.open_orders()) == 2
    fills = broker.fills_since(NOW)
    assert len(fills) == 1
    fake.queue("history", {"data": [{"orders": [filled]}]})
    assert fills == broker.fills_since(NOW)
    history_params = [params for op, params in fake.calls if op == "history"]
    assert history_params[0]["start_time"] == "2026-10-24T12:00:00.000Z"
    assert history_params[1]["pagination_key"] == "page-2"


def test_pagination_cap_and_history_time_limits() -> None:
    broker, fake = setup()
    for _ in range(50):
        fake.queue("open", {"data": [], "pagination_key": "again"})
    with pytest.raises(BrokerUnavailableError, match="50 pages"):
        broker.open_orders()
    assert sum(op == "open" for op, _ in fake.calls) == 50
    broker, fake = setup()
    six_months = NOW.replace(month=4)
    assert broker.fills_since(six_months) == []
    with pytest.raises(ValueError, match="6 months"):
        broker.fills_since(six_months - timedelta(microseconds=1))
    fake.orders["zero"] = order_item("zero")
    fake.orders["old"] = order_item(
        "old", filled_quantity="1", filled_price="1", place_time_at="2026-10-23T12:00:00.000Z"
    )
    assert broker.fills_since(NOW) == []


@pytest.mark.parametrize(
    "delta",
    [
        timedelta(seconds=2) - timedelta(microseconds=1),
        timedelta(seconds=2),
        timedelta(seconds=2) + timedelta(microseconds=1),
    ],
)
def test_query_limiter_threshold(delta: timedelta) -> None:
    clock = SimClock(NOW)
    limiter = QueryLimiter(clock)
    limiter.acquire("balance")
    limiter.acquire("balance")
    limiter.acquire("positions")
    clock.advance(delta)
    if delta < timedelta(seconds=2):
        with pytest.raises(RateLimitedError):
            limiter.acquire("balance")
    else:
        limiter.acquire("balance")


def test_limiter_stops_before_transport() -> None:
    broker, fake = setup()
    fake.advance = False
    broker.account()
    broker.account()
    with pytest.raises(RateLimitedError):
        broker.account()
    assert len(fake.calls) == 2


def test_request_fields_and_id_validation() -> None:
    broker, fake = setup()
    fake.fractional_accept = True
    broker.place_once(order(quantity="0.10001", limit="100"))
    data = fake.calls[-1][1]["new_orders"][0]
    assert data == {
        "combo_type": "NORMAL",
        "client_order_id": "ORDER-1",
        "instrument_type": "EQUITY",
        "market": "US",
        "symbol": "SPY",
        "order_type": "LIMIT",
        "entrust_type": "QTY",
        "side": "BUY",
        "time_in_force": "DAY",
        "support_trading_session": "CORE",
        "quantity": "0.10001",
        "limit_price": "100",
        "account_tax_type": "SPECIFIC",
    }
    market = broker.order_payload(order(limit=None))
    assert "limit_price" not in market
    assert market["order_type"] == "MARKET"
    for invalid in ["a\n", "a" * 33, ""]:
        before = len(fake.calls)
        for action in [broker.cancel, broker.order_status]:
            with pytest.raises(ValueError, match="client_order_id"):
                action(invalid)
        with pytest.raises(ValueError, match="client_order_id"):
            broker.submit(order().model_copy(update={"client_order_id": invalid}))
        assert len(fake.calls) == before
    updates_list: list[dict[str, Any]] = [
        {"extended_hours": True},
        {"order_type": OrderType.STOP},
        {"time_in_force": TimeInForce.GTC},
    ]
    for updates in updates_list:
        with pytest.raises(OrderRejectedError):
            broker.order_payload(order().model_copy(update=updates))
    fake.queue("place", {"client_order_id": "another", "order_id": "broker-1"})
    with pytest.raises(BrokerUnavailableError, match="client_order_id"):
        broker.place_once(order())
    fake.queue("detail", {"orders": [order_item("another"), order_item("ORDER-1")]})
    assert broker.order_status("ORDER-1").client_order_id == "ORDER-1"


def test_configuration_and_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    broker = WebullBroker(SimClock(NOW), transport=FakeTransport(SimClock(NOW)))
    assert broker.environment is BrokerEnvironment.WEBULL_SANDBOX
    (tmp_path / "base.yaml").write_text(
        "webull_sandbox:\n  far_limit_price: 0.50\n  account_tax_type: GENERAL\n"
    )
    settings = load_webull_sandbox_settings(tmp_path)
    assert settings.far_limit_price == Decimal("0.50")
    assert settings.account_tax_type == "GENERAL"
    (tmp_path / "base.yaml").write_text("webull_sandbox:\n  unknown: field\n")
    with pytest.raises(ConfigError, match="invalid webull_sandbox"):
        load_webull_sandbox_settings(tmp_path)
    (tmp_path / "base.yaml").write_text("webull_sandbox:\n  app_key: forbidden\n")
    with pytest.raises(ConfigError, match="credential-like"):
        load_webull_sandbox_settings(tmp_path)


def test_shape_metadata_tolerates_malformed_body() -> None:
    assert shape("balance", {"account_currency_assets": None})["cash_balance"] is False
    assert shape("positions", {})["quantity"] is False
    assert shape("detail", {"orders": [None]})["order_id"] is False
    assert statuses("detail", {"orders": None}) == []
    assert statuses("detail", {"orders": [None, {"status": None}]}) == []
    assert statuses("balance", []) == []


def test_paging_paces_queries_without_repeating_transport() -> None:
    clock = SimClock(NOW)
    fake = FakeTransport(clock)
    fake.advance = False
    waits = []

    def wait(seconds: int) -> None:
        waits.append(seconds)
        clock.advance(timedelta(seconds=seconds))

    broker = WebullBroker(clock, credentials=SandboxCredentials(ENV), transport=fake, wait=wait)
    for key in ["page-2", "page-3", "page-4", None]:
        fake.queue("open", {"data": [], "pagination_key": key})
    assert broker.open_orders() == []
    assert len(fake.calls) == 4
    assert waits == [2]
    assert clock.now() - NOW == timedelta(seconds=2)


@pytest.mark.parametrize("status", [200, 401, 500])
def test_non_json_http_responses_preserve_status(status: int) -> None:
    broker, fake = setup()

    class NonJson(FakeResponse):
        def json(self) -> Any:
            raise ValueError("private response body")

    fake.responses["balance"].append(NonJson({}, status))
    error = BrokerAuthError if status == 401 else BrokerUnavailableError
    with pytest.raises(error) as caught:
        broker.account()
    assert "private" not in str(caught.value)


def test_unknown_codes_and_non_string_messages() -> None:
    broker, fake = setup()
    fake.queue("place", {"message": ["private"]}, 417)
    with pytest.raises(OrderRejectedError) as caught:
        broker.place_once(order())
    assert caught.value.code == "UNKNOWN"
    assert caught.value.known is False
    assert "private" not in str(caught.value)
    fake.queue("place", [1], 500)
    with pytest.raises(BrokerUnavailableError):
        broker.place_once(order())


def test_recursive_sanitisation_and_optional_fees() -> None:
    broker, _ = setup()
    assert broker._safe(1) == 1
    assert broker._safe([ENV["WEBULL_SANDBOX_APP_KEY"]]) == ["[redacted]"]
    fee_values: list[Any] = [None, "", []]
    for fees in fee_values:
        data = order_item(filled_quantity="1", filled_price="1", fees=fees)
        assert parsing.fill(data, parsing.order(data)).costs == ()


def test_additional_shape_metadata() -> None:
    assert shape("balance", None)["cash_balance"] is False
    assert shape("instruments", None)["symbol"] is False
    assert shape("other", {}) == {}
    assert statuses("open", {"data": [{"orders": [order_item()]}]}) == ["SUBMITTED"]
    assert statuses("history", {"data": None}) == []
    assert statuses("open", {"data": [None, {"orders": None}]}) == []


@pytest.mark.parametrize("cid", ["A", "A" * 32])
def test_valid_client_id_boundaries(cid: str) -> None:
    broker, _ = setup()
    assert broker.order_payload(order(cid))["client_order_id"] == cid


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Webull unit tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize("field", list(parsing.BALANCE_FIELDS))
@pytest.mark.parametrize("malformation", ["missing", "invalid"])
def test_required_balance_fields(field: str, malformation: str) -> None:
    broker, fake = setup()
    body = balance()
    asset = body["account_currency_assets"][0]
    if malformation == "missing":
        del asset[field]
    else:
        asset[field] = None if field == "currency" else "not-a-number"
    fake.queue("balance", body)
    with pytest.raises(BrokerUnavailableError, match=field):
        broker.account()


def test_unused_position_numbers_and_required_place_time_are_validated() -> None:
    with pytest.raises(BrokerUnavailableError, match="available_quantity"):
        parsing.positions([position(available_quantity="invalid")])
    with pytest.raises(BrokerUnavailableError, match="place_time_at"):
        parsing.order(order_item(place_time_at="invalid", filled_time_at=NOW.isoformat()))


@pytest.mark.parametrize("kind", ["client", "server", "http"])
def test_documented_rate_limit_code(kind: str) -> None:
    broker, fake = setup()
    if kind == "client":
        fake.responses["place"].append(sdk.ClientException("TOO_MANY_REQUESTS", "private"))
    elif kind == "server":
        fake.responses["place"].append(sdk.ServerException("TOO_MANY_REQUESTS", "private", 417))
    else:
        fake.queue("place", {"error_code": "TOO_MANY_REQUESTS"}, 417)
    with pytest.raises(RateLimitedError) as caught:
        broker.place_once(order())
    assert caught.value.code == "TOO_MANY_REQUESTS"
    assert "private" not in str(caught.value)
    assert len(fake.calls) == 1
