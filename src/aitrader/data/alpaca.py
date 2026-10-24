"""Read-only client for Alpaca historical daily bars (decision Q2: data only, never orders).

Only GET requests to ``data.alpaca.markets`` are made; any other host is refused when the client
is built, so the key can never reach a trading endpoint. Prices are parsed as exact decimals
from the JSON text. Retries happen only for rate limits (429), server errors (5xx) and network
failures, with exponential backoff.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import SecretStr

ALLOWED_HOST = "data.alpaca.markets"
DEFAULT_BASE_URL = f"https://{ALLOWED_HOST}"
BARS_PATH = "/v2/stocks/bars"
NEW_YORK = ZoneInfo("America/New_York")
ADJUSTMENTS = frozenset({"raw", "split", "dividend", "spin-off", "all"})


class DataSourceError(Exception):
    """The data source failed or answered something we cannot use."""


class DataAuthError(DataSourceError):
    """401: the key is missing, wrong or revoked."""


class DataPermissionError(DataSourceError):
    """403: the plan does not include this data (DATA-3)."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(status=exc.code, body=exc.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectionError(f"request failed: {exc}") from exc


@dataclass(frozen=True)
class DailyBarRow:
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def session_date(timestamp: str) -> date:
    """Alpaca stamps daily bars at midnight New York time; that date is the session."""
    return datetime.fromisoformat(timestamp).astimezone(NEW_YORK).date()


class AlpacaDataClient:
    def __init__(
        self,
        key_id: SecretStr,
        secret: SecretStr,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport = urllib_transport,
        timeout_seconds: float = 10.0,
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        page_limit: int = 10_000,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            raise ValueError(f"only https://{ALLOWED_HOST} is allowed, got {base_url!r}")
        if not key_id.get_secret_value() or not secret.get_secret_value():
            raise DataAuthError("the Alpaca key ID and secret are both required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not 1 <= page_limit <= 10_000:
            raise ValueError("page_limit must be between 1 and 10000")
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": secret.get_secret_value(),
            "Accept": "application/json",
        }
        self._transport = transport
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._page_limit = page_limit

    def daily_bars(
        self,
        symbols: Sequence[str],
        start: date,
        end: date,
        *,
        adjustment: str = "raw",
        feed: str = "sip",
    ) -> dict[str, list[DailyBarRow]]:
        """Daily bars for ``symbols`` from ``start`` to ``end`` (inclusive), all pages."""
        if adjustment not in ADJUSTMENTS:
            raise ValueError(f"adjustment must be one of {sorted(ADJUSTMENTS)}")
        if end < start:
            raise ValueError("end must not be before start")
        result: dict[str, list[DailyBarRow]] = {symbol: [] for symbol in symbols}
        params = {
            "symbols": ",".join(sorted(set(symbols))),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "adjustment": adjustment,
            "feed": feed,
            "limit": str(self._page_limit),
            "sort": "asc",
        }
        token: str | None = None
        while True:
            query = dict(params, page_token=token) if token else params
            payload = self._get(f"{self._base_url}{BARS_PATH}?{urllib.parse.urlencode(query)}")
            bars = payload.get("bars")
            if bars is None:  # no data for any symbol on this page
                bars = {}
            if not isinstance(bars, dict):
                raise DataSourceError("unexpected response: 'bars' is not an object")
            for symbol, rows in bars.items():
                if symbol not in result:
                    raise DataSourceError(f"unexpected symbol in response: {symbol}")
                result[symbol].extend(self._row(symbol, row) for row in rows)
            token = payload.get("next_page_token")
            if not token:
                return result

    @staticmethod
    def _row(symbol: str, row: Mapping[str, Any]) -> DailyBarRow:
        try:
            return DailyBarRow(
                session=session_date(row["t"]),
                open=Decimal(row["o"]),
                high=Decimal(row["h"]),
                low=Decimal(row["l"]),
                close=Decimal(row["c"]),
                volume=Decimal(row["v"]),
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise DataSourceError(f"malformed bar for {symbol}: {row!r}") from exc

    def _get(self, url: str) -> dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._transport(url, self._headers, self._timeout)
            except ConnectionError as exc:
                if attempt == self._max_attempts:
                    raise DataSourceError(str(exc)) from exc
                self._sleep(self._backoff * 2 ** (attempt - 1))
                continue
            if response.status == 200:
                try:
                    payload = json.loads(response.body, parse_float=Decimal)
                except ValueError as exc:
                    raise DataSourceError("response is not valid JSON") from exc
                if not isinstance(payload, dict):
                    raise DataSourceError("unexpected response: not a JSON object")
                return payload
            message = response.body[:200].decode("utf-8", errors="replace")
            if response.status == 401:
                raise DataAuthError(f"401 from the data source: {message}")
            if response.status == 403:
                raise DataPermissionError(f"403 from the data source (DATA-3): {message}")
            retryable = response.status == 429 or response.status >= 500
            if not retryable or attempt == self._max_attempts:
                raise DataSourceError(f"HTTP {response.status} from the data source: {message}")
            self._sleep(self._backoff * 2 ** (attempt - 1))
