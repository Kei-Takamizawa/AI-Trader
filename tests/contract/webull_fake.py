"""Handwritten responses from docs/reference/webull-trading-api-v3.md, never recordings."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aitrader.clock import SimClock

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
ENV = {
    "WEBULL_SANDBOX_APP_KEY": "test-key-sensitive",
    "WEBULL_SANDBOX_APP_SECRET": "test-secret-sensitive",
    "WEBULL_SANDBOX_ACCOUNT_ID": "test-account-sensitive",
}


def order_item(cid: str = "order-1", **updates: Any) -> dict[str, Any]:
    return {
        "client_order_id": cid,
        "order_id": "broker-1",
        "symbol": "SPY",
        "side": "BUY",
        "status": "SUBMITTED",
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "total_quantity": "1",
        "place_time_at": "2026-10-24T12:00:00.000Z",
        "account_tax_type": "SPECIFIC",
        "limit_price": "100",
        **updates,
    }


def position(**updates: Any) -> dict[str, Any]:
    return {
        "currency": "USD",
        "quantity": "1",
        "available_quantity": "1",
        "market_value": "100",
        "symbol": "SPY",
        "instrument_id": "instrument-1",
        "instrument_type": "EQUITY",
        "last_price": "100",
        "cost_price": "100",
        "unrealized_profit_loss": "0",
        "base_currency": "JPY",
        "fx_rate": "155",
        "base_currency_market_value": "15500",
        **updates,
    }


def balance(cash: str = "1000", power: str = "1000", jpy: str = "200") -> dict[str, Any]:
    return {
        "account_currency_assets": [
            {
                "currency": "USD",
                "cash_balance": cash,
                "buying_power": power,
                "market_value": "100",
                "unrealized_profit_loss": "0",
            },
            {
                "currency": "JPY",
                "cash_balance": jpy,
                "buying_power": jpy,
                "market_value": "0",
                "unrealized_profit_loss": "0",
            },
        ]
    }


@dataclass
class FakeResponse:
    body: Any
    status_code: int = 200

    def json(self) -> Any:
        return self.body


class FakeTransport:
    def __init__(self, clock: SimClock) -> None:
        self.clock = clock
        self.advance = True
        self.calls: list[tuple[str, Any]] = []
        self.responses: dict[str, deque[FakeResponse | Exception]] = defaultdict(deque)
        self.orders: dict[str, dict[str, Any]] = {}
        self.whole_reject = False
        self.fractional_accept = False
        self.fill_whole = False
        self.final_fill = False
        self.cancel_confirm = True
        self.timeout_accept = False

    def queue(self, operation: str, body: Any, status: int = 200) -> None:
        self.responses[operation].append(FakeResponse(body, status))

    def send(self, request: Any) -> FakeResponse:
        op = request.get_action_name().split("/")[-1]
        paths = {
            "/trading/accounts/list": "accounts",
            "/trading/assets/balances/get": "balance",
            "/trading/assets/positions/list": "positions",
            "/trading/instruments/stocks/profiles/list": "instruments",
            "/trading/orders/get": "detail",
            "/trading/orders/open-orders/list": "open",
            "/trading/orders/historical-orders/list": "history",
        }
        op = paths.get(request.get_action_name(), op)
        params = (
            request.get_body_params()
            if request.get_method() == "POST"
            else request.get_query_params()
        )
        self.calls.append((op, params))
        if self.advance:
            self.clock.advance(timedelta(seconds=2))
        if self.responses[op]:
            result = self.responses[op].popleft()
            if isinstance(result, Exception):
                raise result
            return result
        if op == "accounts":
            return FakeResponse(
                [{"account_id": ENV["WEBULL_SANDBOX_ACCOUNT_ID"], "account_type": "CASH"}]
            )
        if op == "balance":
            return FakeResponse(balance())
        if op == "positions":
            return FakeResponse([position()])
        if op == "instruments":
            return FakeResponse({"data": [{"symbol": "SPY", "fractionable": True, "status": "OC"}]})
        if op == "preview":
            return FakeResponse({"estimated_cost": "1", "estimated_transaction_fee": "0"})
        if op == "place":
            data = params["new_orders"][0]
            cid = data["client_order_id"]
            if cid in self.orders:
                return FakeResponse({"error_code": "DUPLICATE_TEST", "message": "duplicate"}, 417)
            fractional = data["quantity"] not in {"1", "1.00000"}
            if self.whole_reject or (fractional and not self.fractional_accept):
                return FakeResponse({"error_code": "INVALID_PARAMETER", "message": "rule"}, 417)
            self.orders[cid] = order_item(
                cid,
                total_quantity=data["quantity"],
                limit_price=data.get("limit_price"),
                order_type=data["order_type"],
            )
            if self.fill_whole:
                self.orders[cid].update(
                    status="FILLED" if self.final_fill else "PARTIAL_FILLED",
                    filled_quantity="1" if self.final_fill else "0.5",
                    filled_price="1",
                )
            if self.timeout_accept:
                self.timeout_accept = False
                raise TimeoutError("sensitive transport text")
            return FakeResponse({"client_order_id": cid, "order_id": "broker-1"})
        if op == "detail":
            cid = params["client_order_id"]
            return FakeResponse({"orders": [self.orders[cid]] if cid in self.orders else []})
        if op == "cancel":
            cid = params["client_order_id"]
            if self.cancel_confirm:
                self.orders[cid]["status"] = "CANCELLED"
            return FakeResponse({"client_order_id": cid, "order_id": "broker-1"})
        if op in {"open", "history"}:
            return FakeResponse({"data": [{"orders": list(self.orders.values())}]})
        raise RuntimeError(f"unexpected operation: {op}")
