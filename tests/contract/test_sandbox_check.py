"""All sandbox findings and failures are reproducible without the shared service."""

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aitrader.broker.port import BrokerAuthError, BrokerUnavailableError, OrderRejectedError
from aitrader.broker.webull import SandboxCredentials, WebullBroker
from aitrader.broker.webull.check import SandboxCheck
from aitrader.broker.webull.sdk import SDK_LOGGERS
from aitrader.cli import main as cli
from aitrader.clock import SimClock
from tests.contract.webull_fake import ENV, NOW, FakeTransport
from tests.unit.broker.test_sim_broker import order


def setup_check() -> tuple[SandboxCheck, FakeTransport]:
    clock = SimClock(NOW)
    fake = FakeTransport(clock)
    broker = WebullBroker(
        clock,
        settlement_date=lambda day: day + timedelta(days=1),
        credentials=SandboxCredentials(ENV),
        transport=fake,
    )
    check = SandboxCheck(broker, clock, wait=lambda seconds: None)
    broker.set_observer(check.observe)
    return check, fake


@pytest.mark.parametrize(
    ("scenario", "outcome", "result"),
    [
        ("normal", "accepted_cancelled", "pass"),
        ("preview_reject", "rejected", "finding"),
        ("place_reject", "rejected", "finding"),
        ("filled", "filled", "finding"),
        ("fractional_accepted", "accepted_unexpectedly", "finding"),
        ("cancel_unconfirmed", "unavailable", "fail"),
        ("timeout", "unavailable", "fail"),
        ("not_found", "unavailable", "fail"),
    ],
)
def test_order_outcomes(tmp_path: Path, scenario: str, outcome: str, result: str) -> None:
    check, fake = setup_check()
    if scenario == "preview_reject":
        fake.queue("preview", {"error_code": "INVALID_PARAMETER", "message": "rule"}, 417)
    elif scenario == "place_reject":
        fake.whole_reject = True
    elif scenario == "filled":
        fake.fill_whole = True
    elif scenario == "fractional_accepted":
        fake.fractional_accept = True
    elif scenario == "cancel_unconfirmed":
        fake.cancel_confirm = False
    elif scenario == "timeout":
        fake.timeout_accept = True
    elif scenario == "not_found":
        fake.queue("detail", {"orders": []})
    path, exit_code = check.run(tmp_path)
    report = json.loads(path.read_text())
    target = "SB-2 fractional" if scenario == "fractional_accepted" else "SB-2 whole"
    finding = next(item for item in report["checks"] if item["check"] == target)
    assert (finding["outcome"], finding["result"]) == (outcome, result)
    assert exit_code == 1  # SPY flags remain unavailable until the shape is mapped.
    assert report["sdk_version"] == "3.0.4"
    assert report["balances_present"] is True
    assert report["host"] == "jp-openapi-alb.uat.webullbroker.com"
    assert all(
        set(event) == {"operation", "http_status", "error_code", "latency_ms", "shape", "statuses"}
        for event in report["calls"]
    )
    for op, params in fake.calls:
        if op in {"preview", "place"}:
            request = params["new_orders"][0]
            assert request["client_order_id"].startswith("aitsb-")
            assert request["order_type"] == "LIMIT"
            assert request["limit_price"] == "1.00"
    if scenario in {"preview_reject", "place_reject"}:
        duplicate = next(item for item in report["checks"] if item["check"] == "SB-3 duplicate")
        assert duplicate["outcome"] == "skipped"


@pytest.mark.parametrize(
    "scenario",
    [
        "account_missing",
        "flags_missing",
        "auth",
        "no_host",
        "duplicate_accept",
        "duplicate_auth",
        "unknown_auth",
        "unknown_found",
        "unknown_417",
    ],
)
def test_probe_failures_and_duplicate_findings(tmp_path: Path, scenario: str) -> None:
    check, fake = setup_check()
    if scenario == "account_missing":
        fake.queue("accounts", [{"account_id": "other-account"}])
    elif scenario == "flags_missing":
        fake.queue("instruments", {"data": []})
    elif scenario == "auth":
        fake.queue("positions", {}, 401)
    elif scenario == "no_host":

        def unavailable(request: Any) -> Any:
            raise TimeoutError

        fake.send = unavailable  # type: ignore[method-assign]
    elif scenario.startswith("duplicate_"):
        original = check.broker.place_once
        count = 0

        def place(request: Any) -> Any:
            nonlocal count
            count += 1
            if count == 4:
                if scenario == "duplicate_auth":
                    raise BrokerAuthError("refused")
                return None
            return original(request)

        check.broker.place_once = place  # type: ignore[method-assign]
    else:
        original_status = check.broker.order_status

        def status(cid: str) -> Any:
            if cid.startswith("aitsb-unknown-"):
                if scenario == "unknown_auth":
                    raise BrokerAuthError("refused")
                if scenario == "unknown_found":
                    return None
                fake.queue("detail", {"error_code": "UNKNOWN_ORDER_TEST"}, 417)
            return original_status(cid)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(check.broker, "order_status", status)
    path, exit_code = check.run(tmp_path)
    report = json.loads(path.read_text())
    assert exit_code == 1
    if scenario == "no_host":
        assert report["checks"][-1]["result"] == "fail"
    if scenario == "unknown_417":
        assert report["checks"][-2]["error_code"] == "UNKNOWN_ORDER_TEST"


def test_secret_values_never_reach_output_repr_exceptions_or_report(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    check, fake = setup_check()
    broker = check.broker
    # Exercise every SDK logger name at the levels that leak headers and bodies upstream.
    for name in SDK_LOGGERS:
        logging.getLogger(name).error("%s", list(ENV.values()))
        logging.getLogger(name).debug("%s", list(ENV.values()))
    broker.submit(order(quantity="1", limit="100"))
    exceptions = []
    for status, error in [
        (401, BrokerAuthError),
        (417, OrderRejectedError),
        (500, BrokerUnavailableError),
    ]:
        fake.queue(
            "place", {"error_code": "INVALID_PARAMETER", "message": " ".join(ENV.values())}, status
        )
        with pytest.raises(error) as caught:
            broker.place_once(order(quantity="1", limit="100"))
        exceptions.append(str(caught.value))
    path, _ = check.run(tmp_path)
    output = caplog.text + path.read_text() + repr(broker) + repr(SandboxCredentials(ENV))
    output += repr(broker.account()) + " ".join(exceptions)
    assert all(secret not in output for secret in ENV.values())
    assert "[redacted]" in exceptions[1]


@pytest.mark.parametrize("present", [0, 1, 2])
def test_cli_missing_variables_exit_two(monkeypatch: pytest.MonkeyPatch, present: int) -> None:
    for index, (name, value) in enumerate(ENV.items()):
        if index < present:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    result = CliRunner().invoke(cli.app, ["sandbox", "check"])
    assert result.exit_code == 2
    assert "WEBULL_SANDBOX_APP_KEY" in result.output
    assert all(value not in result.output for value in ENV.values())


@pytest.mark.parametrize("fail", [False, True])
def test_cli_fake_transport_reports_and_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    check, fake = setup_check()
    if fail:
        fake.queue("positions", {}, 403)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "WebullBroker", lambda *args, **kwargs: check.broker)
    monkeypatch.setattr(cli, "SandboxCheck", lambda *args: check)
    result = CliRunner().invoke(cli.app, ["sandbox", "check", "--out-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "sandbox report" in result.output
    assert (tmp_path / "2026-10-24.json").exists()
    assert all(secret not in result.output for secret in ENV.values())


def test_final_fill_has_no_remainder_to_cancel(tmp_path: Path) -> None:
    check, fake = setup_check()
    fake.fill_whole = True
    fake.final_fill = True
    path, code = check.run(tmp_path)
    report = json.loads(path.read_text())
    whole = next(item for item in report["checks"] if item["check"] == "SB-2 whole")
    assert whole["outcome"] == "filled"
    assert whole["result"] == "finding"
    assert not any(call["operation"] == "cancel" for call in whole["calls"])
    assert code == 1  # SPY flags remain unavailable.


def test_cancel_reject_is_resolved_by_status(tmp_path: Path) -> None:
    check, fake = setup_check()
    fake.queue("cancel", {"error_code": "INVALID_PARAMETER"}, 417)
    path, code = check.run(tmp_path)
    report = json.loads(path.read_text())
    whole = next(item for item in report["checks"] if item["check"] == "SB-2 whole")
    assert whole["result"] == "fail"
    assert code == 1


@pytest.mark.parametrize(
    ("body", "root_type", "keys", "item_keys"),
    [
        (
            {
                "profiles": [{"symbol": "private-symbol", "secret_value": "private-value"}],
                "page": "private-page",
            },
            "object",
            ["page", "profiles"],
            ["secret_value", "symbol"],
        ),
        ([{"z": "private-value", "a": "private-value"}], "array", [], ["a", "z"]),
        ({"data": [], "other": [{"ignored": "private-value"}]}, "object", ["data", "other"], []),
        ({"data": ["private-value"]}, "object", ["data"], []),
        ({"meta": "private-value"}, "object", ["meta"], []),
        ([], "array", [], []),
        (None, "unknown", [], []),
    ],
)
def test_instrument_shape_names_only_and_flags_unavailable(
    tmp_path: Path, body: Any, root_type: str, keys: list[str], item_keys: list[str]
) -> None:
    check, fake = setup_check()
    fake.queue("instruments", body)
    path, code = check.run(tmp_path)
    contents = path.read_text()
    report = json.loads(contents)
    event = next(call for call in report["calls"] if call["operation"] == "instruments")
    assert event["shape"]["root_type"] == root_type
    assert event["shape"]["top_level_keys"] == keys
    assert event["shape"]["first_item_keys"] == item_keys
    flags = next(item for item in report["checks"] if item["check"] == "SB-1 SPY flags")
    assert (flags["result"], flags["outcome"]) == ("fail", "unavailable")
    assert code == 1
    assert all(
        value not in contents for value in ("private-value", "private-page", "private-symbol")
    )


@pytest.mark.parametrize("terminal", ["CANCELED", "CANCELLED", "FILLED", "FAILED"])
def test_whole_terminal_status_never_sends_cancel(tmp_path: Path, terminal: str) -> None:
    from tests.contract.webull_fake import FakeResponse, order_item

    check, fake = setup_check()
    original = fake.send

    def send(request: Any) -> FakeResponse:
        if request.get_action_name() == "/trading/orders/get":
            cid = request.get_query_params()["client_order_id"]
            if cid in fake.orders:
                fake.orders[cid] = order_item(cid, status=terminal)
        return original(request)

    fake.send = send  # type: ignore[method-assign]
    path, _ = check.run(tmp_path)
    whole = next(
        item for item in json.loads(path.read_text())["checks"] if item["check"] == "SB-2 whole"
    )
    assert whole["result"] == "pass"
    assert not any(op == "cancel" for op, _ in fake.calls)


def test_observed_cancel_and_fractional_probe_payloads(tmp_path: Path) -> None:
    check, fake = setup_check()
    path, code = check.run(tmp_path)
    report = json.loads(path.read_text())
    places = [params["new_orders"][0] for op, params in fake.calls if op == "place"]
    assert [item["quantity"] for item in places] == ["1.00000", "1.50000", "0.00001", "1.00000"]
    assert sum(op == "cancel" for op, _ in fake.calls) == 1
    whole = next(item for item in report["checks"] if item["check"] == "SB-2 whole")
    assert whole["outcome"] == "accepted_cancelled"
    assert any("CANCELED" in call["statuses"] for call in whole["calls"])
    fractional = next(item for item in report["checks"] if item["check"] == "SB-2 fractional")
    assert (fractional["outcome"], fractional["result"]) == ("rejected_as_expected", "pass")
    duplicate = next(item for item in report["checks"] if item["check"] == "SB-3 duplicate")
    assert duplicate["error_code"] == "OPENAPI_REPEAT_REQUEST"
    assert code == 1
