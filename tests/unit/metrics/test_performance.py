"""Metrics against hand-computed values."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from aitrader.domain import Bar, Currency, Fill, Money, Side, TradeCost, TradeCostType
from aitrader.fees import WEBULL_JP, ExecutionAssumptions, FeeSchedule
from aitrader.metrics import (
    EquityPoint,
    buy_and_hold,
    daily_returns,
    fmt,
    max_drawdown,
    period_returns,
    sharpe,
    sortino,
    summarize,
    trade_pnls,
    trade_stats,
)
from aitrader.portfolio import Ledger

D = Decimal
T0 = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)


def points(values: list[str], start: date = date(2024, 1, 30)) -> list[EquityPoint]:
    return [
        EquityPoint(session=start + timedelta(days=i), equity_usd=D(v), market_value_usd=D(0))
        for i, v in enumerate(values)
    ]


def test_fmt() -> None:
    assert fmt(None, 2) is None
    assert fmt(D(0), 4) == "0.0000"
    assert fmt(D("1.005"), 2) == "1.00"  # half even
    assert fmt(D("1.015"), 2) == "1.02"


def test_daily_returns() -> None:
    assert daily_returns(D(100), points(["110", "99"])) == [D("0.1"), D(99) / D(110) - 1]


def test_max_drawdown_and_duration() -> None:
    drawdown, sessions = max_drawdown(D(100), points(["110", "99", "105", "111", "100"]))
    assert drawdown == D("0.1")  # 110 -> 99
    assert sessions == 2
    assert max_drawdown(D(100), []) == (D(0), 0)


def test_period_returns_chain_from_the_previous_period() -> None:
    monthly = period_returns(
        D(100),
        points(["101", "102", "103"], start=date(2024, 1, 30)),
        lambda d: d.strftime("%Y-%m"),
    )
    assert monthly == {"2024-01": D("0.02"), "2024-02": D(103) / D(102) - 1}


def test_sharpe_and_sortino() -> None:
    assert sharpe([D("0.01")] * 5) is None  # no variation
    assert sharpe([]) is None
    value = sharpe([D("0.01"), D("-0.01"), D("0.02")])
    assert value is not None
    assert value > 0
    assert sortino([D("0.01"), D("0.02")]) is None  # no downside
    assert sortino([]) is None
    down = sortino([D("0.02"), D("-0.01")])
    assert down is not None
    # mean 0.005, downside deviation sqrt(0.0001 / 2)
    assert down == D("0.005") / (D("0.00005").sqrt()) * D(252).sqrt()


def test_trade_stats() -> None:
    assert trade_stats([]) == {
        "count": "0",
        "wins": "0",
        "losses": "0",
        "win_rate_pct": None,
        "profit_factor": None,
        "payoff_ratio": None,
    }
    stats = trade_stats([D(3), D(-1), D(1), D(-2)])
    assert stats["win_rate_pct"] == "50.00"
    assert stats["profit_factor"] == "1.3333"
    assert stats["payoff_ratio"] == "1.3333"
    only_wins = trade_stats([D(1)])
    assert only_wins["profit_factor"] is None
    assert only_wins["payoff_ratio"] is None


def _ledger_with_a_round_trip() -> Ledger:
    ledger = Ledger()
    ledger.deposit("dep", T0, Money.of(1000, Currency.USD))
    buy = Fill(
        fill_id="b",
        client_order_id="o1",
        symbol="SPY",
        side=Side.BUY,
        quantity=D(1),
        price=D(100),
        ts=T0,
        source="sim",
    )
    sell = Fill(
        fill_id="s",
        client_order_id="o2",
        symbol="SPY",
        side=Side.SELL,
        quantity=D(1),
        price=D(110),
        ts=T0,
        source="sim",
    )
    ledger.record_fill(buy)
    fee = TradeCost(
        ts=T0,
        type=TradeCostType.SEC_FEE,
        amount=Money.of("0.01", Currency.USD),
        estimated=True,
        source="model",
    )
    ledger.record_fill(sell, [fee], settles_on=date(2024, 1, 3))
    return ledger


def test_trade_pnls_are_net_of_sale_fees() -> None:
    assert trade_pnls(_ledger_with_a_round_trip()) == [D("9.99")]


def test_buy_and_hold_benchmark_pays_slippage_and_fees() -> None:
    bars = [
        Bar(
            symbol="SPY",
            ts=T0 + timedelta(days=i),
            open=D(o),
            high=max(D(o), D(c)),
            low=min(D(o), D(c)),
            close=D(c),
            volume=D(1),
        )
        for i, (o, c) in enumerate([("100", "100"), ("100", "110")])
    ]
    curve = buy_and_hold(bars, D(100), WEBULL_JP, ExecutionAssumptions(slippage_bps_per_side=D(0)))
    assert len(curve) == 2
    quantity = curve[0].market_value_usd / 100
    assert quantity == D("0.99999")  # 1 share would leave nothing for the CAT fee
    assert curve[1].equity_usd > curve[0].equity_usd
    assert buy_and_hold([], D(100), WEBULL_JP, ExecutionAssumptions()) == []
    costly = FeeSchedule(name="flat", commission_rate=D("0.5"), commission_min_usd=D(0))
    assert buy_and_hold(bars[:1], D(1), costly, ExecutionAssumptions())[0].market_value_usd < 1


def test_summary_structure_and_values() -> None:
    ledger = _ledger_with_a_round_trip()
    curve = points(["1005", "1009.99"])
    summary = summarize(
        start_equity=D(1000),
        points=curve,
        ledger=ledger,
        fx_jpy_per_usd=D(155),
        traded_notional_usd=D(210),
        spread_and_slippage_usd=D("0.5"),
        prices={},
        benchmark_symbol="SPY",
        benchmark=points(["1000", "1100"]),
    )
    assert summary["capital"] == {
        "start_usd": "1000.00",
        "end_usd": "1009.99",
        "fx_jpy_per_usd": "155.00",
    }
    returns = summary["returns"]
    assert isinstance(returns, dict)
    assert returns["total_pct"] == "0.9990"
    costs = summary["costs"]
    assert isinstance(costs, dict)
    assert costs["fees_usd"] == {"SEC_FEE": "0.01000000"}
    assert costs["total_usd"] == "0.51000000"
    assert costs["fx_usd"] == "0.00000000"
    pnl = summary["pnl"]
    assert isinstance(pnl, dict)
    assert pnl["trading_pnl_usd"] == "9.990000"
    benchmarks = summary["benchmarks"]
    assert isinstance(benchmarks, dict)
    assert benchmarks["B0_buy_and_hold"]["total_pct"] == "10.0000"


def test_summary_of_an_empty_run() -> None:
    summary = summarize(
        start_equity=D(100),
        points=[],
        ledger=Ledger(),
        fx_jpy_per_usd=D(155),
        traded_notional_usd=D(0),
        spread_and_slippage_usd=D(0),
        prices={},
        benchmark_symbol="SPY",
        benchmark=[],
    )
    assert summary["period"] == {"start": None, "end": None, "sessions": "0"}
    returns = summary["returns"]
    assert isinstance(returns, dict)
    assert returns["cagr_pct"] is None
    assert returns["share_of_months_at_least_1pct"] is None
    costs = summary["costs"]
    assert isinstance(costs, dict)
    assert costs["share_of_gross_pnl_pct"] is None
