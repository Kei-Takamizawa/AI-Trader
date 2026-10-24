"""Alpaca client: allow-list, request shape, pagination, retries, errors and parsing."""

import re
import threading
import urllib.parse
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from pydantic import SecretStr

from aitrader.data import AlpacaDataClient, DataAuthError, DataPermissionError, DataSourceError
from aitrader.data.alpaca import HttpResponse, session_date, urllib_transport
from tests.unit.data.alpaca_fakes import FakeTransport, bar, page, status

KEY = SecretStr("key-id")
SECRET = SecretStr("secret-key")
START, END = date(2024, 1, 2), date(2024, 1, 3)


def client(transport: FakeTransport, **kw: Any) -> AlpacaDataClient:
    sleeps: list[float] = kw.pop("sleeps", [])
    return AlpacaDataClient(KEY, SECRET, transport=transport, sleep=sleeps.append, **kw)


SPY_PAGE = page(
    {
        "SPY": [
            bar("2024-01-02T05:00:00Z", "472.16", "473.67", "470.49", "472.65", 123007793),
            bar("2024-01-03T05:00:00Z", "470.43", "471.19", "468.17", "468.79", 103585902),
        ]
    }
)


def test_request_shape_and_headers() -> None:
    transport = FakeTransport([SPY_PAGE])
    result = client(transport).daily_bars(["SPY", "QQQ"], START, END, adjustment="all")
    url, headers, timeout = transport.requests[0]
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    assert (parts.scheme, parts.hostname, parts.path) == (
        "https",
        "data.alpaca.markets",
        "/v2/stocks/bars",
    )
    assert query == {
        "symbols": "QQQ,SPY",
        "timeframe": "1Day",
        "start": "2024-01-02",
        "end": "2024-01-03",
        "adjustment": "all",
        "feed": "sip",
        "limit": "10000",
        "sort": "asc",
    }
    assert headers["APCA-API-KEY-ID"] == "key-id"
    assert headers["APCA-API-SECRET-KEY"] == "secret-key"
    assert "secret-key" not in url
    assert timeout == 10.0
    assert result["QQQ"] == []
    assert [r.session for r in result["SPY"]] == [START, END]


def test_prices_are_exact_decimals() -> None:
    (first, _) = client(FakeTransport([SPY_PAGE])).daily_bars(["SPY"], START, END)["SPY"]
    assert first.close == Decimal("472.65")
    assert first.open == Decimal("472.16")
    assert first.volume == Decimal(123007793)


def test_daily_timestamps_map_to_new_york_sessions() -> None:
    assert session_date("2024-01-02T05:00:00Z") == date(2024, 1, 2)  # winter, EST
    assert session_date("2024-07-01T04:00:00Z") == date(2024, 7, 1)  # summer, EDT


def test_all_pages_are_read() -> None:
    transport = FakeTransport(
        [
            page({"QQQ": [bar("2024-01-02T05:00:00Z", "1", "1", "1", "1", 1)]}, next_page="abc"),
            page({"SPY": [bar("2024-01-02T05:00:00Z", "2", "2", "2", "2", 2)]}),
        ]
    )
    result = client(transport).daily_bars(["SPY", "QQQ"], START, END)
    assert len(transport.requests) == 2
    assert "page_token=abc" in transport.requests[1][0]
    assert (len(result["QQQ"]), len(result["SPY"])) == (1, 1)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://data.alpaca.markets",
        "https://paper-api.alpaca.markets",  # the trading API: never
        "https://api.alpaca.markets",
        "https://data.alpaca.markets.evil.example",
    ],
)
def test_only_the_market_data_host_is_allowed(base_url: str) -> None:
    with pytest.raises(ValueError, match=re.escape("only https://data.alpaca.markets")):
        AlpacaDataClient(KEY, SECRET, base_url=base_url)


def test_credentials_are_required() -> None:
    with pytest.raises(DataAuthError, match="both required"):
        AlpacaDataClient(SecretStr(""), SECRET)


@pytest.mark.parametrize("kw", [{"page_limit": 0}, {"page_limit": 10_001}, {"max_attempts": 0}])
def test_client_settings_are_checked(kw: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must"):
        AlpacaDataClient(KEY, SECRET, **kw)  # type: ignore[arg-type]


def test_request_arguments_are_checked() -> None:
    c = client(FakeTransport([]))
    with pytest.raises(ValueError, match="adjustment"):
        c.daily_bars(["SPY"], START, END, adjustment="weird")
    with pytest.raises(ValueError, match="before start"):
        c.daily_bars(["SPY"], END, START)


def test_rate_limits_and_server_errors_are_retried_with_backoff() -> None:
    sleeps: list[float] = []
    transport = FakeTransport([status(429), status(503), ConnectionError("reset"), SPY_PAGE])
    result = client(transport, sleeps=sleeps).daily_bars(["SPY"], START, END)
    assert len(result["SPY"]) == 2
    assert sleeps == [1.0, 2.0, 4.0]


def test_retries_end_after_the_last_attempt() -> None:
    with pytest.raises(DataSourceError, match="HTTP 500"):
        client(FakeTransport([status(500)] * 4)).daily_bars(["SPY"], START, END)
    with pytest.raises(DataSourceError, match="request failed"):
        client(FakeTransport([ConnectionError("request failed: down")] * 4)).daily_bars(
            ["SPY"], START, END
        )


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (401, DataAuthError),
        (403, DataPermissionError),
        (400, DataSourceError),
        (422, DataSourceError),
    ],
)
def test_client_errors_are_not_retried(code: int, error: type[Exception]) -> None:
    transport = FakeTransport([status(code, "nope")])
    with pytest.raises(error, match="nope"):
        client(transport).daily_bars(["SPY"], START, END)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (HttpResponse(200, b"not json"), "not valid JSON"),
        (HttpResponse(200, b"[]"), "not a JSON object"),
        (HttpResponse(200, b'{"bars": [], "next_page_token": null}'), "'bars' is not an object"),
        (page({"DIA": [bar("2024-01-02T05:00:00Z", "1", "1", "1", "1", 1)]}), "unexpected symbol"),
        (page({"SPY": [{"t": "2024-01-02T05:00:00Z", "o": 1}]}), "malformed bar"),
    ],
)
def test_unusable_responses_are_refused(response: HttpResponse, message: str) -> None:
    with pytest.raises(DataSourceError, match=message):
        client(FakeTransport([response])).daily_bars(["SPY"], START, END)


def test_empty_bars_are_allowed() -> None:
    result = client(FakeTransport([HttpResponse(200, b'{"bars": null, "next_page_token": null}')]))
    assert result.daily_bars(["SPY"], START, END) == {"SPY": []}


# ---------------------------------------------------------------- the real transport, locally


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        code = 200 if self.path == "/ok" else 404
        body = b'{"ok": true}' if code == 200 else b"missing"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def test_urllib_transport_against_a_local_server() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert urllib_transport(f"{base}/ok", {"X": "1"}, 5) == HttpResponse(200, b'{"ok": true}')
        assert urllib_transport(f"{base}/other", {}, 5) == HttpResponse(404, b"missing")
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(ConnectionError, match="request failed"):
        urllib_transport(f"{base}/ok", {}, 1)  # nothing listens any more
