"""Synthetic boundary checks for weekly rotation and parameter provenance."""

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from aitrader.strategy import (
    Holding,
    MomentumParams,
    MomentumStrategy,
    StrategyContext,
    params_hash,
)
from tests.unit.strategy.test_pullback import series

D = Decimal
P = MomentumParams(symbols=("SPY", "IVV", "QQQ"), trend_period=3, lookback=2, atr_period=2)


def ctx(prices: dict[str, list[str]], held: tuple[str, ...] = ()) -> StrategyContext:
    return StrategyContext(
        session=date(2023, 4, 6),
        as_of=series(["100"])[0].ts,
        next_session=date(2023, 4, 10),
        history={
            s: tuple(b.model_copy(update={"symbol": s}) for b in series(v))
            for s, v in prices.items()
        },
        holdings={
            s: Holding(symbol=s, quantity=D("0.5"), entry_session=date(2023, 1, 3)) for s in held
        },
        equity_usd=D("323.03"),
    )


def test_review_holidays_week_year_and_fallback() -> None:
    c = ctx({"SPY": ["100", "101", "103"]})
    strategy = MomentumStrategy(P)
    assert strategy.evaluate(c)  # Thursday before Good Friday
    assert strategy.evaluate(c.model_copy(update={"next_session": None}))
    assert not strategy.evaluate(c.model_copy(update={"next_session": date(2023, 4, 7)}))
    assert strategy.evaluate(
        c.model_copy(update={"session": date(2022, 12, 30), "next_session": date(2023, 1, 3)})
    )


@pytest.mark.parametrize(
    ("last", "eligible"), [("100.0001", True), ("100", False), ("99.9999", False)]
)
def test_trend_boundary(last: str, eligible: bool) -> None:
    assert bool(MomentumStrategy(P).evaluate(ctx({"SPY": ["100", "100", last]}))) == eligible


def test_ties_equivalents_and_held_representative() -> None:
    c = ctx({s: ["100", "101", "103"] for s in P.symbols})
    assert [i.symbol for i in MomentumStrategy(P).evaluate(c)] == ["IVV", "QQQ"]
    held = c.model_copy(update={"holdings": ctx({}, ("SPY",)).holdings})
    assert [i.symbol for i in MomentumStrategy(P).evaluate(held)] == ["QQQ"]
    ineligible = ctx({"SPY": ["103", "102", "100"], "IVV": ["100", "101", "103"]}, ("SPY",))
    assert [i.symbol for i in MomentumStrategy(P).evaluate(ineligible)] == ["SPY", "IVV"]


def test_skip_changes_rank() -> None:
    c = ctx({"SPY": ["90", "100", "110", "106"], "QQQ": ["100", "100", "101", "110"]})
    p = replace(P, top_n=1)
    assert MomentumStrategy(p).evaluate(c)[0].symbol == "QQQ"
    assert MomentumStrategy(replace(p, skip=1)).evaluate(c)[0].symbol == "SPY"


def test_exits_reasons_hold_and_entry_rounding() -> None:
    c = ctx({"SPY": ["103", "102", "100"], "QQQ": ["100", "101", "103"]}, ("SPY", "IVV", "QQQ"))
    intents = MomentumStrategy(P).evaluate(c)
    assert len(intents) == 2
    assert intents[0].symbol == "IVV"
    assert "insufficient history" in intents[0].reason
    assert intents[1].target_quantity == D("0.5")
    assert "trend average" in intents[1].reason
    c = ctx({"SPY": ["100", "101", "103"], "QQQ": ["100", "102", "105"]}, ("SPY",))
    intents = MomentumStrategy(replace(P, top_n=1)).evaluate(c)
    assert "top ranks" in intents[0].reason
    assert intents[1].target_notional is not None
    assert intents[1].target_notional.amount == D("80.76")
    assert intents[1].stop_price == D("94.50")  # ATR = (3 + 4)/2


def test_cash_insufficient_history_and_nonpositive_stop() -> None:
    assert len(MomentumStrategy(P).evaluate(ctx({"SPY": ["100", "101", "103"]}))) == 1
    assert MomentumStrategy(P).evaluate(ctx({"SPY": ["100"]})) == []
    assert MomentumStrategy(P).evaluate(ctx({})) == []
    assert (
        MomentumStrategy(replace(P, atr_stop_multiple=D(100))).evaluate(
            ctx({"SPY": ["100", "101", "103"]})
        )
        == []
    )


def test_all_fields_affect_hash() -> None:
    p = MomentumParams()
    assert MomentumStrategy().strategy_id == "S1"
    assert MomentumStrategy().version == "1.0.0"
    for f in fields(p):
        value = getattr(p, f.name)
        changed: Any = () if isinstance(value, tuple) else value + 1
        assert params_hash(MomentumStrategy(replace(p, **{f.name: changed}))) != params_hash(
            MomentumStrategy(p)
        )


def test_held_symbol_outside_universe_exits_as_ineligible() -> None:
    intents = MomentumStrategy(P).evaluate(ctx({}, ("GLD",)))
    assert len(intents) == 1
    assert intents[0].reason == "no longer eligible: outside strategy universe"
