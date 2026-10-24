"""Sandbox-only BrokerPort adapter; submit ambiguity is resolved by client ID lookup."""

import calendar
import re
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from time import sleep
from typing import Any

from aitrader.broker.port import (
    AccountSnapshot,
    BrokerAuthError,
    BrokerCapabilities,
    BrokerError,
    BrokerFill,
    BrokerOrder,
    BrokerOrderAck,
    BrokerPosition,
    BrokerUnavailableError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitedError,
)
from aitrader.broker.webull import parsing
from aitrader.broker.webull.known_codes import KNOWN_CODES
from aitrader.broker.webull.sdk import (
    ClientException,
    SDKTransport,
    ServerException,
    Transport,
    build_request,
    silence_sdk,
    unknown_token_facts,
)
from aitrader.broker.webull.settings import (
    SandboxCredentials,
    WebullSandboxSettings,
    validate_host,
)
from aitrader.clock import Clock
from aitrader.config.errors import LiveDisabledError
from aitrader.domain import BrokerEnvironment, OrderRequest, OrderStatus, OrderType, TimeInForce
from aitrader.domain.ids import CLIENT_ORDER_ID_PATTERN
from aitrader.domain.types import require_utc

STEP = Decimal("0.00001")


class QueryLimiter:
    """Separate rolling windows for balance, positions and each order query endpoint."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._calls: dict[str, deque[datetime]] = defaultdict(deque)

    def wait_seconds(self, operation: str) -> int:
        now = self._clock.now()
        calls = self._calls[operation]
        while calls and now - calls[0] >= timedelta(seconds=2):
            calls.popleft()
        if len(calls) < 2:
            return 0
        remaining = timedelta(seconds=2) - (now - calls[0])
        return max(0, -(-remaining // timedelta(seconds=1)))

    def acquire(self, operation: str) -> None:
        now = self._clock.now()
        calls = self._calls[operation]
        while calls and now - calls[0] >= timedelta(seconds=2):
            calls.popleft()
        if len(calls) >= 2:
            raise RateLimitedError("local query limit: 2 per 2 seconds")
        calls.append(now)


class WebullBroker:
    def __init__(
        self,
        clock: Clock,
        *,
        settlement_date: Callable[[date], date],
        environment: BrokerEnvironment = BrokerEnvironment.WEBULL_SANDBOX,
        settings: WebullSandboxSettings | None = None,
        credentials: SandboxCredentials | None = None,
        transport: Transport | None = None,
        observe: Callable[[dict[str, Any]], None] | None = None,
        wait: Callable[[int], None] = sleep,
    ) -> None:
        if environment is not BrokerEnvironment.WEBULL_SANDBOX:
            raise LiveDisabledError("WebullBroker requires the sandbox environment")
        self.settings = settings or WebullSandboxSettings()
        self.host = validate_host(self.settings.endpoint)
        silence_sdk()
        self._credentials = credentials or SandboxCredentials()
        self._transport = transport or SDKTransport(self._credentials, self.host, clock)
        self._clock = clock
        self._settlement_date = settlement_date
        self._limiter = QueryLimiter(clock)
        self._observe = observe
        self._wait = wait

    @property
    def token_facts(self) -> dict[str, Any]:
        return dict(getattr(self._transport, "token_facts", unknown_token_facts()))

    def set_observer(self, observe: Callable[[dict[str, Any]], None]) -> None:
        self._observe = observe

    @property
    def environment(self) -> BrokerEnvironment:
        return BrokerEnvironment.WEBULL_SANDBOX

    def close(self) -> None:
        if isinstance(self._transport, SDKTransport):
            self._transport.close()

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            fractional_orders=True,
            fractional_step=STEP,
            min_fractional_buy_usd=Decimal(1),
            order_types=frozenset({OrderType.LIMIT, OrderType.MARKET}),
        )

    def _safe(self, value: Any) -> Any:
        """Redact before parsing as even a status or an error code can echo credentials."""
        if isinstance(value, str):
            return self._credentials.redact(value)
        if isinstance(value, dict):
            return {key: self._safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._safe(item) for item in value]
        return value

    def call(self, operation: str, **params: Any) -> Any:
        """One SDK attempt; the observer receives metadata only, never response bodies."""
        if operation in {"balance", "positions", "detail", "open", "history"}:
            delay = self._limiter.wait_seconds(operation)
            if delay:
                self._wait(delay)
            self._limiter.acquire(operation)
        started = self._clock.now()
        status: int | None = None
        code: str | None = None
        body: Any = None
        try:
            request = build_request(
                operation, self._credentials.account_id.get_secret_value(), params
            )
            try:
                response = self._transport.send(request)
                status = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    if 200 <= status < 300:
                        raise BrokerUnavailableError("response") from None
                    body = {}
            except ServerException as exc:
                status = exc.get_http_status()
                body = {
                    "error_code": self._safe(exc.get_error_code()),
                    "message": self._safe(exc.get_error_msg()),
                }
            except ClientException as exc:
                code = self._safe(exc.get_error_code())
                self._raise_http(operation, None, code, {})
            except OSError, ValueError:
                raise BrokerUnavailableError("transport or response error") from None
            if isinstance(body, dict) and isinstance(body.get("error_code"), str):
                code = self._safe(body["error_code"])
            if status is None or not 200 <= status < 300:
                self._raise_http(operation, status, code, self._safe(body))
            return body
        finally:
            if self._observe is not None:
                self._observe(
                    {
                        "operation": operation,
                        "http_status": status,
                        "error_code": code,
                        "latency_ms": str(
                            (self._clock.now() - started) // timedelta(microseconds=1000)
                        ),
                        "shape": self._safe(shape(operation, body)),
                        "statuses": self._safe(statuses(operation, body)),
                    }
                )

    @staticmethod
    def _raise_http(operation: str, status: int | None, code: str | None, body: Any) -> None:
        message = body.get("message", "") if isinstance(body, dict) else ""
        if not isinstance(message, str):
            message = ""
        if status in {401, 403}:
            error: BrokerError = BrokerAuthError("sandbox authentication refused")
        elif status == 429 or code == "TOO_MANY_REQUESTS":
            error = RateLimitedError("sandbox rate limit")
        elif status == 417 and operation == "detail":
            error = OrderNotFoundError("client_order_id")
        elif status == 417 and operation in {"preview", "place", "cancel"}:
            error = OrderRejectedError(code or "UNKNOWN", message, known=code in KNOWN_CODES)
        else:
            error = BrokerUnavailableError("sandbox HTTP error")
        error.http_status = status
        if not isinstance(error, OrderRejectedError):
            error.code = code
        raise error from None

    def account(self) -> AccountSnapshot:
        body = parsing.obj(self.call("balance"), "balance")
        settled = unsettled = jpy = Decimal(0)
        for raw in parsing.array(body.get("account_currency_assets"), "account_currency_assets"):
            item = parsing.obj(raw, "account_currency_assets")
            for field in parsing.BALANCE_FIELDS:
                parsing.text(item, field)
            for field in ("cash_balance", "buying_power", "market_value", "unrealized_profit_loss"):
                parsing.number(item, field)
            currency = parsing.text(item, "currency")
            cash = parsing.number(item, "cash_balance")
            power = parsing.number(item, "buying_power")
            if currency == "USD":
                settled = max(Decimal(0), min(cash, power))
                unsettled = max(Decimal(0), cash - settled)
            elif currency == "JPY":
                jpy = max(Decimal(0), cash)
        return AccountSnapshot(
            account_id=self._credentials.account_id.get_secret_value(),
            environment=self.environment,
            ts=self._clock.now(),
            cash_usd_settled=settled,
            cash_usd_unsettled=unsettled,
            cash_jpy=jpy,
        )

    def positions(self) -> list[BrokerPosition]:
        return parsing.positions(self.call("positions"))

    def _orders(self, operation: str, **params: Any) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        for _ in range(50):
            body = parsing.obj(self.call(operation, **params), operation)
            for raw in parsing.array(body.get("data"), "data"):
                group = parsing.obj(raw, "data")
                orders.extend(
                    parsing.obj(item, "orders")
                    for item in parsing.array(group.get("orders"), "orders")
                )
            key = body.get("pagination_key")
            if not parsing.present(key):
                return orders
            params["pagination_key"] = parsing.text(body, "pagination_key")
        raise BrokerUnavailableError("pagination_key: more than 50 pages")

    def open_orders(self) -> list[BrokerOrder]:
        return [parsing.order(self._safe(item)) for item in self._orders("open")]

    @staticmethod
    def _validate_id(client_order_id: str) -> None:
        if re.fullmatch(CLIENT_ORDER_ID_PATTERN, client_order_id) is None:
            raise ValueError("invalid client_order_id")

    def order_status(self, client_order_id: str) -> BrokerOrder:
        self._validate_id(client_order_id)
        body = parsing.obj(self.call("detail", client_order_id=client_order_id), "detail")
        for raw in parsing.array(body.get("orders"), "orders"):
            item = parsing.obj(raw, "orders")
            if item.get("client_order_id") == client_order_id:
                return parsing.order(self._safe(item))
        raise OrderNotFoundError("client_order_id")

    def order_payload(self, request: OrderRequest) -> dict[str, str]:
        self._validate_id(request.client_order_id)
        if (
            request.order_type not in self.capabilities().order_types
            or request.extended_hours
            or request.time_in_force is not TimeInForce.DAY
        ):
            raise OrderRejectedError("INVALID_PARAMETER", "only CORE DAY MARKET/LIMIT supported")
        if request.quantity % STEP:
            raise OrderRejectedError("INVALID_PARAMETER", "invalid fractional quantity step")
        payload = {
            "combo_type": "NORMAL",
            "client_order_id": request.client_order_id,
            "instrument_type": "EQUITY",
            "market": "US",
            "symbol": request.symbol,
            "order_type": request.order_type.value,
            "entrust_type": "QTY",
            "side": request.side.value,
            "time_in_force": "DAY",
            "support_trading_session": "CORE",
            "quantity": format(request.quantity.quantize(STEP), "f"),
            "account_tax_type": self.settings.account_tax_type,
        }
        if request.limit_price is not None:
            payload["limit_price"] = format(request.limit_price, "f")
        return payload

    def preview(self, request: OrderRequest) -> None:
        self.call("preview", new_orders=[self.order_payload(request)])

    def place_once(self, request: OrderRequest) -> BrokerOrderAck:
        """The operator's duplicate probe must bypass the adapter's idempotent lookup."""
        body = parsing.obj(self.call("place", new_orders=[self.order_payload(request)]), "place")
        if parsing.text(body, "client_order_id") != request.client_order_id:
            raise BrokerUnavailableError("client_order_id")
        return BrokerOrderAck(
            client_order_id=request.client_order_id,
            broker_order_id=parsing.text(body, "order_id"),
            status=OrderStatus.SUBMITTED,
        )

    def submit(self, request: OrderRequest) -> BrokerOrderAck:
        self.order_payload(request)
        try:
            existing = self.order_status(request.client_order_id)
        except OrderNotFoundError:
            try:
                return self.place_once(request)
            except OrderRejectedError as exc:
                if exc.code != "OPENAPI_REPEAT_REQUEST":
                    raise
                try:
                    existing = self.order_status(request.client_order_id)
                except OrderNotFoundError:
                    raise exc from None
        return BrokerOrderAck(
            client_order_id=existing.client_order_id,
            broker_order_id=existing.broker_order_id,
            status=existing.status,
        )

    def cancel(self, client_order_id: str) -> None:
        self._validate_id(client_order_id)
        self.call("cancel", client_order_id=client_order_id)

    def fills_since(self, ts: datetime) -> list[BrokerFill]:
        ts = require_utc(ts)
        now = self._clock.now()
        month_index = now.year * 12 + now.month - 1 - 6
        year, month = divmod(month_index, 12)
        month += 1
        earliest = now.replace(
            year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1])
        )
        if ts < earliest:
            raise ValueError("order history reaches back at most 6 months")
        # Revisit terminal orders whose last execution predates the manager cursor.
        start = max(ts - timedelta(days=7), earliest)
        result = {}
        for item in self._orders(
            "history", start_time=start.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        ):
            snapshot = parsing.order(self._safe(item))
            if (
                snapshot.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
                and snapshot.filled_quantity > 0
            ):
                execution = parsing.fill(item, snapshot, self._settlement_date, now)
                result[execution.fill.fill_id] = execution
        return list(result.values())


def order_rows(operation: str, body: Any) -> list[Any]:
    if not isinstance(body, dict):
        return []
    if operation == "detail":
        rows = body.get("orders")
        return rows if isinstance(rows, list) else []
    if operation in {"open", "history"}:
        groups = body.get("data")
        if isinstance(groups, list):
            return [
                item
                for group in groups
                if isinstance(group, dict) and isinstance(group.get("orders"), list)
                for item in group["orders"]
            ]
    return []


def statuses(operation: str, body: Any) -> list[str]:
    return [
        item["status"]
        for item in order_rows(operation, body)
        if isinstance(item, dict) and isinstance(item.get("status"), str)
    ]


def shape(operation: str, body: Any) -> dict[str, Any]:
    """Report field presence; instruments additionally expose names without values."""
    fields: tuple[str, ...] = ()
    rows: Any = []
    if operation == "balance":
        fields = parsing.BALANCE_FIELDS
        rows = body.get("account_currency_assets") if isinstance(body, dict) else []
    elif operation == "positions":
        fields, rows = parsing.POSITION_FIELDS, body
    elif operation in {"detail", "open", "history"}:
        fields, rows = parsing.ORDER_FIELDS, order_rows(operation, body)
    elif operation == "accounts":
        fields, rows = ("account_id", "account_type"), body
    elif operation == "instruments":
        fields = (
            "symbol",
            "instrument_id",
            "category",
            "status",
            "fractionable",
            "shortable",
            "marginable",
            "overnight_trading_supported",
            "lot_size",
            "currency",
            "sub_category",
        )
        rows = body.get("data") if isinstance(body, dict) else []
    elif operation in {"place", "cancel"}:
        fields, rows = ("client_order_id", "order_id"), [body]
    elif operation == "preview":
        fields, rows = ("estimated_cost", "estimated_transaction_fee"), [body]
    if not isinstance(rows, list):
        rows = []
    findings: dict[str, Any] = {}
    if operation == "instruments":
        findings["root_type"] = (
            "object" if isinstance(body, dict) else "array" if isinstance(body, list) else "unknown"
        )
        findings["top_level_keys"] = sorted(body) if isinstance(body, dict) else []
        lists = (
            [body]
            if isinstance(body, list)
            else (
                [value for value in body.values() if isinstance(value, list)]
                if isinstance(body, dict)
                else []
            )
        )
        first = lists[0][0] if lists and lists[0] else None
        findings["first_item_keys"] = sorted(first) if isinstance(first, dict) else []
    findings.update(
        {
            field: bool(rows)
            and all(isinstance(row, dict) and parsing.present(row.get(field)) for row in rows)
            for field in fields
        }
    )
    return findings
