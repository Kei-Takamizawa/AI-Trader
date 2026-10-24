"""S2 pullback strategy: entry, exit and stop rules; same bars give the same intents."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from aitrader.domain import Bar, IntentPurpose, Side
from aitrader.strategy import (
    Holding,
    PullbackParams,
    PullbackStrategy,
    StrategyContext,
    params_hash,
)

D = Decimal
START = datetime(2023, 1, 2, 14, 30, tzinfo=UTC)
PARAMS = PullbackParams(symbols=("SPY",), trend_period=15, atr_period=3, exit_sma_period=3)


def series(closes: list[str]) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol="SPY",
            ts=START + timedelta(days=i),
            open=D(c),
            high=D(c) + 1,
            low=D(c) - 1,
            close=D(c),
            volume=D(1_000_000),
        )
        for i, c in enumerate(closes)
    )


UPTREND_THEN_DIP = [str(100 + 2 * i) for i in range(15)] + ["126", "124", "120"]
"""Close 120 is above the 15-session average (118.27) and RSI(2) is 8.3."""


def context(closes: list[str], holdings: dict[str, Holding] | None = None) -> StrategyContext:
    bars = series(closes)
    return StrategyContext(
        session=bars[-1].ts.date(),
        as_of=bars[-1].ts + timedelta(hours=7),
        history={"SPY": bars},
        holdings=holdings or {},
        equity_usd=D(323),
    )


def test_entry_on_a_pullback_in_an_uptrend() -> None:
    (intent,) = PullbackStrategy(PARAMS).evaluate(context(UPTREND_THEN_DIP))
    assert intent.side is Side.BUY
    assert intent.purpose is IntentPurpose.ENTRY
    assert intent.target_notional is not None
    assert intent.target_notional.amount == D("80.75")  # 25% of USD 323
    # ATR(3): true ranges 3, 3, 5 -> 11/3; stop 120 - 2.5 * 11/3 = 110.83
    assert intent.stop_price == D("110.83")
    assert intent.limit_price is None
    assert intent.signal_ts == series(UPTREND_THEN_DIP)[-1].ts
    assert "RSI(2)" in intent.reason


def test_no_entry_without_a_pullback_or_below_the_trend() -> None:
    rising = [str(100 + 2 * i) for i in range(18)]
    assert PullbackStrategy(PARAMS).evaluate(context(rising)) == []
    falling = [str(200 - 2 * i) for i in range(18)]
    assert PullbackStrategy(PARAMS).evaluate(context(falling)) == []


def test_not_enough_history_means_no_signal() -> None:
    assert PullbackStrategy(PARAMS).evaluate(context(["100", "99"])) == []
    empty = StrategyContext(session=date(2023, 1, 2), as_of=START, history={}, equity_usd=D(323))
    assert PullbackStrategy(PARAMS).evaluate(empty) == []


def test_stop_below_zero_means_no_entry() -> None:
    params = PullbackParams(
        symbols=("SPY",), trend_period=15, atr_period=3, atr_stop_multiple=D(1000)
    )
    assert PullbackStrategy(params).evaluate(context(UPTREND_THEN_DIP)) == []


def holding(entry_offset: int, closes: list[str]) -> dict[str, Holding]:
    entry = series(closes)[entry_offset].ts.date()
    return {"SPY": Holding(symbol="SPY", quantity=D("0.5"), entry_session=entry)}


def test_exit_when_the_close_is_above_the_short_average() -> None:
    closes = [*UPTREND_THEN_DIP, "125"]
    (intent,) = PullbackStrategy(PARAMS).evaluate(context(closes, holding(-2, closes)))
    assert intent.side is Side.SELL
    assert intent.purpose is IntentPurpose.EXIT
    assert intent.target_quantity == D("0.5")
    assert "3-day average" in intent.reason


def test_exit_after_the_maximum_holding_time() -> None:
    closes = [*UPTREND_THEN_DIP, "119", "118", "117", "116", "115"]
    (intent,) = PullbackStrategy(PARAMS).evaluate(context(closes, holding(-6, closes)))
    assert "held 5 sessions" in intent.reason


def test_hold_while_no_exit_rule_applies() -> None:
    closes = [*UPTREND_THEN_DIP, "119"]
    assert PullbackStrategy(PARAMS).evaluate(context(closes, holding(-2, closes))) == []


def test_same_bars_give_the_same_intents() -> None:
    first = PullbackStrategy(PARAMS).evaluate(context(UPTREND_THEN_DIP))
    second = PullbackStrategy(PARAMS).evaluate(context(UPTREND_THEN_DIP))
    assert first == second
    assert first[0].intent_id == second[0].intent_id


def test_identity_and_parameters() -> None:
    strategy = PullbackStrategy()
    assert (strategy.strategy_id, strategy.version) == ("S2", "1.0.0")
    assert strategy.params()["trend_period"] == "200"
    assert params_hash(strategy) == params_hash(PullbackStrategy())
    assert params_hash(strategy) != params_hash(PullbackStrategy(PARAMS))
