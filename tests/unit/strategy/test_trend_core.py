"""S3 slot allocation, ordering and strict trend/reduction boundaries."""

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from aitrader.strategy import (
    Holding,
    StrategyContext,
    TrendCoreParams,
    TrendCoreStrategy,
    params_hash,
)
from tests.unit.strategy.test_momentum import ctx

D = Decimal
P = TrendCoreParams(trend_period=3, atr_period=2)
UP = ["100", "101", "103"]
DOWN = ["103", "102", "100"]


def context(prices: dict[str, list[str]], held: tuple[str, ...] = ()) -> StrategyContext:
    c = ctx(prices, held)
    return c.model_copy(update={"session": date(2023, 1, 4), "next_session": None})


def test_review_and_all_wanted() -> None:
    c = context({s: UP for s in P.core})
    strategy = TrendCoreStrategy(P)
    assert [i.symbol for i in strategy.evaluate(c)] == sorted(P.core)
    assert strategy.evaluate(c.model_copy(update={"next_session": date(2023, 1, 5)})) == []
    assert strategy.evaluate(c.model_copy(update={"next_session": date(2023, 1, 9)}))
    assert strategy.evaluate(context({s: UP for s in P.core}, P.core)) == []


def test_two_slots_and_exit_order() -> None:
    c = context({"SPY": DOWN, "QQQ": UP, "GLD": UP, "SHY": DOWN}, ("SPY", "IWM"))
    intents = TrendCoreStrategy(P).evaluate(c)
    assert [(i.symbol, i.side.value) for i in intents] == [
        ("IWM", "SELL"),
        ("SPY", "SELL"),
        ("SHY", "BUY"),
        ("GLD", "BUY"),
        ("QQQ", "BUY"),
    ]
    assert intents[0].reason == "no bar on the session"
    assert intents[1].reason == "below the trend average"
    assert intents[0].target_quantity == D("0.5")
    proxy = intents[2]
    assert proxy.target_notional is not None
    assert proxy.target_notional.amount == D("161.52")
    assert proxy.stop_price == D("92.50")
    assert proxy.reason == "defensive asset for 2 unwanted core slots"


@pytest.mark.parametrize(
    ("last", "wanted"), [("100.0001", True), ("100", False), ("99.9999", False)]
)
def test_strict_trend(last: str, wanted: bool) -> None:
    intents = TrendCoreStrategy(P).evaluate(context({"SPY": ["100", "100", last]}))
    assert bool(intents) == wanted


@pytest.mark.parametrize(
    ("prices", "reason"),
    [
        ([], "no bar"),
        (["100"], "not enough trend history"),
        (["100", "101", "103"], "not enough ATR history"),
    ],
)
def test_unwanted_reasons(prices: list[str], reason: str) -> None:
    c = context({"SPY": prices}, ("SPY",))
    if prices:
        c = c.model_copy(update={"session": c.history["SPY"][-1].ts.date()})
    intents = TrendCoreStrategy(replace(P, atr_period=3)).evaluate(c)
    assert reason in intents[0].reason


def test_stale_core_and_defensive_history_and_stop() -> None:
    c = context({"SPY": UP, "SHY": UP}, ("SPY",))
    stale = c.model_copy(update={"session": date(2023, 1, 5)})
    assert TrendCoreStrategy(P).evaluate(stale)[0].reason == "no bar on the session"
    assert len(TrendCoreStrategy(P).evaluate(context({"SHY": ["100"]}))) == 0
    one = context({"SHY": ["100"]})
    one = one.model_copy(update={"session": one.history["SHY"][-1].ts.date()})
    assert TrendCoreStrategy(P).evaluate(one) == []
    assert (
        TrendCoreStrategy(replace(P, atr_stop_multiple=D(100))).evaluate(
            context({"SPY": UP, "SHY": DOWN})
        )
        == []
    )
    # Exactly zero and just above zero after cent quantization.
    for stop_multiple, expected in [(D("50"), False), (D("49.995"), True)]:
        assert (
            bool(
                TrendCoreStrategy(replace(P, atr_stop_multiple=stop_multiple)).evaluate(
                    context({"SHY": ["100", "100", "100"]})
                )
            )
            == expected
        )


@pytest.mark.parametrize(
    ("quantity", "sell"), [("2.99999", False), ("3", False), ("3.00001", True)]
)
def test_defensive_reduction_boundary(quantity: str, sell: bool) -> None:
    c = context({"SPY": UP, "QQQ": UP, "GLD": UP, "SHY": DOWN}, ("SHY",))
    c = c.model_copy(
        update={
            "equity_usd": D(1000),
            "holdings": {
                "SHY": Holding(symbol="SHY", quantity=D(quantity), entry_session=date(2023, 1, 3))
            },
        }
    )
    intents = TrendCoreStrategy(P).evaluate(c)
    sales = [i for i in intents if i.symbol == "SHY"]
    assert bool(sales) == sell
    if sell:
        assert sales[0].target_quantity == D("0.50001")
        assert sales[0].reason == "reduce the defensive asset"


def test_defensive_exit_missing_marks_and_zero_rounding() -> None:
    strategy = TrendCoreStrategy(P)
    intents = strategy.evaluate(context({s: UP for s in P.core}, ("SHY",)))
    assert intents[0].symbol == "SHY"
    assert intents[0].target_quantity == D("0.5")
    assert strategy.evaluate(context({}, ("SHY",))) == []
    c = context({"SHY": DOWN}, ("SHY",))
    assert strategy.evaluate(c) == []  # never top up
    tiny = c.model_copy(
        update={
            "equity_usd": D("0.0001"),
            "holdings": {
                "SHY": Holding(
                    symbol="SHY", quantity=D("0.0000011"), entry_session=date(2023, 1, 3)
                )
            },
        }
    )
    assert strategy.evaluate(tiny) == []


def test_identity() -> None:
    p = TrendCoreParams()
    strategy = TrendCoreStrategy()
    assert (strategy.strategy_id, strategy.version) == ("S3", "1.0.0")
    assert params_hash(strategy) == params_hash(TrendCoreStrategy(p))
    for field in fields(p):
        value = getattr(p, field.name)
        changed: Any = (
            () if isinstance(value, tuple) else ("BIL" if isinstance(value, str) else value + 1)
        )
        assert params_hash(TrendCoreStrategy(replace(p, **{field.name: changed}))) != params_hash(
            strategy
        )
