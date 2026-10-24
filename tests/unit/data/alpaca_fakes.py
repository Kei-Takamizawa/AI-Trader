"""Recorded-format responses for the Alpaca bars endpoint (shape from the official docs)."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from aitrader.data.alpaca import HttpResponse


def bar(t: str, o: str, h: str, low: str, c: str, v: int) -> dict[str, object]:
    # numbers are written as JSON numbers, exactly as the API sends them
    return {
        "t": t,
        "o": float(o),
        "h": float(h),
        "l": float(low),
        "c": float(c),
        "v": v,
        "n": 1,
        "vw": float(c),
    }


def page(bars: Mapping[str, list[dict[str, object]]], next_page: str | None = None) -> HttpResponse:
    body = json.dumps({"bars": bars, "next_page_token": next_page, "currency": "USD"})
    return HttpResponse(status=200, body=body.encode())


def status(code: int, message: str = "error") -> HttpResponse:
    return HttpResponse(status=code, body=json.dumps({"message": message}).encode())


@dataclass
class FakeTransport:
    responses: list[HttpResponse | Exception]
    requests: list[tuple[str, dict[str, str], float]] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        self.requests.append((url, dict(headers), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
