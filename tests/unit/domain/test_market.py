from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aitrader.domain import Bar, Instrument, InstrumentKind, Quote

TS = datetime(2026, 10, 22, 13, 30, tzinfo=UTC)


def _bar(**overrides: object) -> Bar:
    fields: dict[str, object] = {
        "symbol": "SPY",
        "ts": TS,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1000",
    }
    fields.update(overrides)
    return Bar.model_validate(fields)


def test_valid_bar() -> None:
    bar = _bar()
    assert bar.close == Decimal(101)
    assert bar.source == "unknown"


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": "98"},  # high below low
        {"high": "100.5"},  # high below close
        {"low": "100.5"},  # low above open
        {"open": "0"},  # non-positive price
        {"close": "-1"},
        {"volume": "-5"},  # negative volume
        {"ts": datetime(2026, 10, 22, 13, 30)},  # noqa: DTZ001 - naive datetime
        {"symbol": "spy"},  # lower case symbol
        {"symbol": "TOO-LONG-SYMBOL"},
        {"close": 101.0},  # float price
    ],
)
def test_invalid_bars_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _bar(**overrides)


def test_bar_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _bar(vwap="100")


def test_quote_validation() -> None:
    quote = Quote(symbol="QQQ", ts=TS, bid="400.10", ask="400.12", bid_size=5, ask_size=7)  # type: ignore[arg-type]
    assert quote.ask > quote.bid
    with pytest.raises(ValidationError):
        Quote(symbol="QQQ", ts=TS, bid="400.12", ask="400.10", bid_size=5, ask_size=7)  # type: ignore[arg-type]


def test_instrument_defaults() -> None:
    etf = Instrument(symbol="BRK.B", kind=InstrumentKind.COMMON_STOCK)
    assert etf.quantity_increment == Decimal(1)
    assert not etf.fractionable
    with pytest.raises(ValidationError):
        Instrument(symbol="SPY", kind=InstrumentKind.ETF, quantity_increment="0")  # type: ignore[arg-type]
