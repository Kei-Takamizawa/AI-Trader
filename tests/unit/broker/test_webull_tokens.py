"""Exercise the SDK initializer and signed HTTP path without opening a socket."""

import json
import logging
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from aitrader.broker.port import BrokerAuthError, BrokerUnavailableError, OrderRejectedError
from aitrader.broker.webull import SandboxCredentials, WebullBroker, sdk
from aitrader.broker.webull.check import SandboxCheck
from aitrader.broker.webull.settings import SANDBOX_HOST
from aitrader.cli import main as cli
from aitrader.clock import SimClock
from aitrader.config import ConfigError
from tests.contract.webull_fake import ENV, NOW, FakeResponse, FakeTransport
from tests.unit.broker.test_sim_broker import order

ACCESS_VALUE = "invented-private-access-token"


class InitializerHTTP:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.clock = SimClock(NOW)
        self.trading = FakeTransport(self.clock)
        self.enabled: Any = True
        self.verification_status: Any = "NORMAL"
        self.config_status = 200
        self.failure: Exception | None = None
        self.calls: list[tuple[str, bool]] = []
        self.clients: list[Any] = []
        self.raw_response = False
        self.config_body: Any = None
        self.token_body: Any = None
        monkeypatch.setattr(sdk.ApiClient, "_handle_single_request", self.single)

    def single(self, endpoint: str, request: Any, read: Any, connect: Any, signer: Any) -> Any:
        # The bound fake receives no client; use the captured constructor instance instead.
        client = self.clients[-1]
        assert endpoint == SANDBOX_HOST
        http = client._make_http_response(endpoint, request, read, connect, signer)
        self.calls.append((request.get_action_name(), "x-access-token" in http.get_headers()))
        self.clock.advance(timedelta(milliseconds=100))
        if self.failure:
            raise self.failure
        path = request.get_action_name()
        if path == "/openapi/config":
            body = (
                self.config_body
                if self.config_body is not None
                else {"token_check_enabled": self.enabled}
            )
            status = self.config_status
            if status != 200:
                body = {"error_code": "UNAUTHORIZED", "message": ACCESS_VALUE}
        elif path.startswith("/auth/tokens/"):
            body = (
                self.token_body
                if self.token_body is not None
                else {
                    "token": ACCESS_VALUE,
                    "expires": "9999999999",
                    "status": self.verification_status,
                }
            )
            status = 200
        else:
            response = self.trading.send(request)
            body, status = response.body, response.status_code
        encoded = json.dumps(body).encode()
        exception = (
            None
            if self.raw_response
            else client._get_server_exception(request, status, {}, encoded, endpoint, "")
        )
        return status, {}, encoded, exception, FakeResponse(body, status)


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> InitializerHTTP:
    import socket

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("token tests must not connect to the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    fake = InitializerHTTP(monkeypatch)
    real_client = sdk.ApiClient

    def client(*args: Any, **kwargs: Any) -> Any:
        result = real_client(*args, **kwargs)
        fake.clients.append(result)
        return result

    monkeypatch.setattr(sdk, "ApiClient", client)
    return fake


def broker_for(http: InitializerHTTP) -> WebullBroker:
    return WebullBroker(
        http.clock,
        settlement_date=lambda day: day + timedelta(days=1),
        credentials=SandboxCredentials(ENV),
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_sdk_token_enabled_normal_or_disabled(http: InitializerHTTP, enabled: bool) -> None:
    http.enabled = enabled
    broker = broker_for(http)
    client = http.clients[-1]
    token_dir = Path(client.get_token_dir())
    assert client.get_token_check_duration_seconds() == 10
    assert client.get_token_check_interval_seconds() == 5
    assert broker.token_facts == {
        "token_check_enabled": enabled,
        "status": "NORMAL" if enabled else None,
        "expires_present": enabled,
        "http_status": 200,
        "error_code": None,
        "latency_ms": "200" if enabled else "100",
    }
    assert token_dir.exists()
    assert token_dir.stat().st_mode & 0o777 == 0o700
    if enabled:
        assert list(token_dir.rglob("*"))
    assert broker.account().cash_usd_settled == 1000
    assert http.calls[-1] == ("/trading/assets/balances/get", enabled)
    assert [path for path, _ in http.calls].count("/openapi/config") == 1
    broker.close()
    assert not token_dir.exists()


def test_pending_token_stops_within_bounded_sdk_poll(
    http: InitializerHTTP, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    manager = importlib.import_module("webull.core.http.initializer.token.token_manager")
    elapsed = 0
    waits = []

    def sleep(seconds: int) -> None:
        nonlocal elapsed
        elapsed += seconds
        waits.append(seconds)
        http.clock.advance(timedelta(seconds=seconds))

    # Patch this SDK module only; never replace Python's global clock or wait in real time.
    monkeypatch.setattr(manager, "time", SimpleNamespace(time=lambda: elapsed, sleep=sleep))
    http.verification_status = "PENDING"
    with pytest.raises(BrokerAuthError) as caught:
        broker_for(http)
    assert isinstance(caught.value, sdk.TokenInitializationError)
    assert caught.value.code == "ERROR_INIT_TOKEN"
    assert caught.value.facts["status"] == "PENDING"
    assert caught.value.facts["token_check_enabled"] is True
    assert waits == [5, 5]
    assert elapsed == 10
    assert all("trading" not in path for path, _ in http.calls)
    assert not Path(http.clients[-1].get_token_dir()).exists()
    assert ACCESS_VALUE not in str(caught.value)


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("raw", [False, True])
def test_config_auth_failure_stops_cli_and_writes_report(
    http: InitializerHTTP,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
    raw: bool,
) -> None:
    http.config_status = status
    http.raw_response = raw
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "WallClock", lambda: http.clock)
    result = CliRunner().invoke(cli.app, ["sandbox", "check", "--out-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert len(http.calls) == 1
    report = json.loads((tmp_path / "2026-10-24.json").read_text())
    token = report["checks"][0]
    assert token["check"] == "token"
    assert token["result"] == "fail"
    assert token["error_code"] == "UNAUTHORIZED"
    assert token["http_status"] == status
    assert token["latency_ms"] == "100"
    assert token["token_check_enabled"] is None
    assert all(
        item["outcome"] == "skipped_auth" and item["result"] == "fail"
        for item in report["checks"][1:]
    )
    assert not Path(http.clients[-1].get_token_dir()).exists()
    assert all(
        secret not in result.output + json.dumps(report) for secret in (*ENV.values(), ACCESS_VALUE)
    )


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("operation", ["accounts", "balance", "positions", "instruments"])
def test_sb1_auth_stops_every_remaining_call(
    http: InitializerHTTP, tmp_path: Path, operation: str, status: int
) -> None:
    http.trading.queue(operation, {"error_code": "UNAUTHORIZED"}, status)
    broker = broker_for(http)
    check = SandboxCheck(broker, http.clock, wait=lambda seconds: None)
    broker.set_observer(check.observe)
    path, code = check.run(tmp_path)
    report = json.loads(path.read_text())
    checks = report["checks"]
    failure_index = next(index for index, item in enumerate(checks) if item["result"] == "fail")
    assert checks[failure_index]["error_code"] == "UNAUTHORIZED"
    assert checks[failure_index]["calls"][-1]["operation"] == operation
    assert all(item["outcome"] == "skipped_auth" for item in checks[failure_index + 1 :])
    assert http.trading.calls[-1][0] == operation
    assert all(op not in {"place", "preview", "cancel"} for op, _ in http.trading.calls)
    assert code == 1
    broker.close()


def test_token_is_redacted_from_sdk_logs_errors_status_and_report(
    http: InitializerHTTP,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.DEBUG)
    broker = broker_for(http)
    check = SandboxCheck(broker, http.clock, wait=lambda seconds: None)
    broker.set_observer(check.observe)
    broker.submit(order(quantity="1", limit="100"))
    secrets = (*ENV.values(), ACCESS_VALUE)
    outputs = []
    for status, error in [
        (401, BrokerAuthError),
        (417, OrderRejectedError),
        (500, BrokerUnavailableError),
    ]:
        http.trading.queue(
            "place", {"error_code": "INVALID_PARAMETER", "message": " ".join(secrets)}, status
        )
        with pytest.raises(error) as caught:
            broker.place_once(order())
        outputs.append(str(caught.value))
    # Unknown statuses and error codes also pass through the same redaction set.
    http.trading.queue(
        "detail", {"orders": [{**http.trading.orders["ORDER-1"], "status": ACCESS_VALUE}]}
    )
    broker.order_status("ORDER-1")
    path, _ = check.run(tmp_path)
    output = caplog.text + path.read_text() + repr(broker) + repr(broker._credentials)
    output += " ".join(outputs)
    assert all(secret not in output for secret in secrets)
    assert "[redacted]" in outputs[1]
    broker.close()


def test_wrong_resolved_host_never_calls_initializer(
    http: InitializerHTTP, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sdk.ClientInitializer.initializer
    called = []

    def initialize(client: Any) -> None:
        called.append(True)
        original(client)

    monkeypatch.setattr(sdk.ClientInitializer, "initializer", initialize)
    real_client = sdk.ApiClient

    def client(*args: Any, **kwargs: Any) -> Any:
        result = real_client(*args, **kwargs)
        result.add_endpoint = lambda *args: None
        return result

    monkeypatch.setattr(sdk, "ApiClient", client)
    with pytest.raises(ConfigError):
        broker_for(http)
    assert not called
    assert not http.calls


@pytest.mark.parametrize(
    "error_code",
    ["ERROR_INIT_TOKEN", "ERROR_CREATE_TOKEN", "ERROR_CHECK_TOKEN", "ERROR_CHECK_TOKEN_ENABLE"],
)
def test_sdk_initialization_error_code_is_kept_without_message(
    http: InitializerHTTP,
    error_code: str,
) -> None:
    http.failure = sdk.ClientException(error_code, ACCESS_VALUE)
    with pytest.raises(sdk.TokenInitializationError) as caught:
        broker_for(http)
    assert caught.value.code == error_code
    assert ACCESS_VALUE not in str(caught.value)
    assert not Path(http.clients[-1].get_token_dir()).exists()


@pytest.mark.parametrize("failure", [TimeoutError(ACCESS_VALUE), ValueError(ACCESS_VALUE)])
def test_initialization_io_or_json_failure_is_safe(
    http: InitializerHTTP, failure: Exception
) -> None:
    http.failure = failure
    with pytest.raises(sdk.TokenInitializationError) as caught:
        broker_for(http)
    assert ACCESS_VALUE not in str(caught.value)
    assert not Path(http.clients[-1].get_token_dir()).exists()


@pytest.mark.parametrize(
    "config_body", [[], {"token_check_enabled": "unknown"}, {"other": "present"}]
)
def test_unknown_token_config_metadata(http: InitializerHTTP, config_body: Any) -> None:
    http.config_body = config_body
    if not config_body:
        with pytest.raises(sdk.TokenInitializationError):
            broker_for(http)
    else:
        broker = broker_for(http)
        assert broker.token_facts["token_check_enabled"] is None
        broker.close()


@pytest.mark.parametrize(("field", "value"), [("token", ""), ("status", None), ("expires", None)])
def test_incomplete_token_response_fails_closed(
    http: InitializerHTTP, field: str, value: Any
) -> None:
    http.token_body = {
        "token": ACCESS_VALUE,
        "status": "NORMAL",
        "expires": "9999999999",
        field: value,
    }
    with pytest.raises(sdk.TokenInitializationError) as caught:
        broker_for(http)
    assert caught.value.code == "ERROR_CREATE_TOKEN"
    assert ACCESS_VALUE not in str(caught.value)


def test_host_is_checked_again_immediately_before_initializer(
    http: InitializerHTTP,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = sdk.ApiClient

    def client(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)

        def change_host(path: str) -> None:
            result.add_endpoint("jp", "api.webull.co.jp")
            result._token_dir = path

        result.set_token_dir = change_host
        return result

    monkeypatch.setattr(sdk, "ApiClient", client)
    with pytest.raises(ConfigError):
        broker_for(http)
    assert not http.calls
    assert not Path(http.clients[-1].get_token_dir()).exists()


@pytest.mark.parametrize("enabled", [True, False])
def test_cli_success_reports_only_token_facts(
    http: InitializerHTTP,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    enabled: bool,
) -> None:
    http.enabled = enabled
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "WallClock", lambda: http.clock)
    monkeypatch.setattr(
        cli,
        "SandboxCheck",
        lambda broker, clock: SandboxCheck(broker, clock, wait=lambda seconds: None),
    )
    result = CliRunner().invoke(cli.app, ["sandbox", "check", "--out-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    report_text = (tmp_path / "2026-10-24.json").read_text()
    facts = json.loads(report_text)["checks"][0]
    assert facts["check"] == "token"
    assert facts["token_check_enabled"] is enabled
    assert facts["status"] == ("NORMAL" if enabled else None)
    assert facts["expires_present"] is enabled
    assert facts["error_code"] is None
    assert facts["result"] == "pass"
    assert "9999999999" not in report_text
    assert all(value not in report_text + result.output for value in (*ENV.values(), ACCESS_VALUE))
    assert not Path(http.clients[-1].get_token_dir()).exists()
