"""Declared axes, reporting-only neighbours and independent-fold cost accounting."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aitrader.backtest import metrics_for, run_backtest
from aitrader.backtest.walkforward import (
    neighbours,
    run_walkforward,
    same_sign,
    variants,
)
from aitrader.data import DailyBarSource
from aitrader.strategy import PullbackStrategy, params_hash
from tests.integration.test_backtest import CAL, cli, settings

D = Decimal


def test_declared_axes_and_neighbours() -> None:
    for name, keys, values in [
        ("S1", ("top_n", "skip"), [("3", "0"), ("3", "5"), ("2", "0"), ("2", "5")]),
        (
            "S3",
            ("trend_period", "atr_stop_multiple"),
            [("200", "3"), ("200", "2"), ("150", "3"), ("150", "2")],
        ),
    ]:
        grid = variants(name)
        assert [tuple(v.params()[k] for k in keys) for v in grid] == values
        assert [list(grid).index(v) for v in neighbours(grid, 0, name)] == [1, 2]
        assert [list(grid).index(v) for v in neighbours(grid, 3, name)] == [1, 2]
    assert neighbours(variants("S2"), 0, "S2") == []
    with pytest.raises(ValueError, match="only supported for S1"):
        variants("S3", "SHY")


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [(1, 2, True), (-1, -2, True), (0, 0, True), (0, 1, False), (0, -1, False), (-1, 1, False)],
)
def test_three_distinct_signs(left: int, right: int, expected: bool) -> None:
    assert same_sign(D(left), D(right)) == expected


def test_snapshot_opt_in() -> None:
    source = DailyBarSource(Path("tests/fixtures/bars"), CAL)
    kwargs: Any = {"start": date(2023, 10, 2), "end": date(2023, 10, 6), "calendar": CAL}
    plain = run_backtest(settings(), PullbackStrategy(), source, **kwargs)
    captured = run_backtest(settings(), PullbackStrategy(), source, capture_sessions=True, **kwargs)
    assert plain.sessions == []
    assert len(captured.sessions) == len(captured.points)
    assert plain.points == captured.points
    assert plain.intents == captured.intents
    assert metrics_for(plain, source, settings()) == metrics_for(captured, source, settings())


@pytest.mark.parametrize("strategy", ["S1", "S2", "S3"])
def test_fixture_reporting_and_neighbours_are_reporting_only(
    strategy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aitrader.backtest.walkforward as wf

    source = DailyBarSource(Path("tests/fixtures/bars"), CAL)
    options: Any = dict(
        start=date(2023, 1, 3),
        train_months=6,
        test_months=3,
        holdout_months=3,
        calendar=CAL,
        risk_per_trade_pct=D(1),
        prior_variants=21,
    )
    _, report = run_walkforward(settings(), strategy, source, **options)
    folds: Any = report["folds"]
    summary: Any = report["fold_summary"]
    combined: Any = report["combined"]
    for fold, row in zip(folds, summary, strict=True):
        assert row["stable"] == fold["stable"]
        assert (
            row["cost_share_of_gross_pnl_pct"]
            == fold["test_metrics"]["costs"]["share_of_gross_pnl_pct"]
        )
        for neighbour in fold["neighbours"]:
            variant = next(
                v for v in variants(strategy) if params_hash(v) == neighbour["params_hash"]
            )
            assert neighbour["params"] == dict(variant.params())
            result = run_backtest(
                settings().model_copy(
                    update={"risk": settings().risk.model_copy(update={"risk_per_trade_pct": D(1)})}
                ),
                variant,
                source,
                start=date.fromisoformat(fold["test"]["start"]),
                end=date.fromisoformat(fold["test"]["end_exclusive"]) - timedelta(days=1),
                calendar=CAL,
            )
            actual: Any = metrics_for(result, source, settings())["returns"]
            assert neighbour["total_pct"] == actual["total_pct"]
        if fold["neighbours"]:
            assert fold["stable"] == all(
                same_sign(D(fold["test_metrics"]["returns"]["total_pct"]), D(n["total_pct"]))
                for n in fold["neighbours"]
            )
        else:
            assert fold["stable"] is None
    assert combined["stability"]["folds"] == (0 if strategy == "S2" else len(folds))
    assert D(combined["costs"]["total_usd"]) == sum(
        D(f["test_metrics"]["costs"]["total_usd"]) for f in folds
    )
    monkeypatch.setattr(wf, "neighbours", lambda *args: [])
    without: Any
    _, without = run_walkforward(settings(), strategy, source, **options)
    assert report["equity"] == without["equity"]
    for key in ("returns", "risk", "benchmarks", "costs"):
        assert combined[key] == without["combined"][key]
    assert [f["selected_variant"] for f in folds] == [
        f["selected_variant"] for f in without["folds"]
    ]


def test_s3_cli_and_cash_proxy_refusal(tmp_path: Path) -> None:
    base = (
        "walkforward",
        "--data-dir",
        "tests/fixtures/bars",
        "--strategy",
        "S3",
        "--out-dir",
        str(tmp_path / "out"),
    )
    rejected = cli(*base, "--cash-proxy", "SHY")
    assert rejected.exit_code == 5
    assert "only supported for S1" in rejected.output
    result = cli(
        *base,
        "--start",
        "2023-01-03",
        "--train-months",
        "6",
        "--test-months",
        "3",
        "--holdout-months",
        "3",
        "--risk-per-trade-pct",
        "1.0",
        "--prior-variants",
        "21",
    )
    assert result.exit_code == 0, result.output


def test_neighbours_exclude_nonadjacent_axis_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    from aitrader.backtest.walkforward import GRID_AXES
    from aitrader.strategy import MomentumParams, MomentumStrategy

    monkeypatch.setitem(GRID_AXES, "S1", {"top_n": (3, 2, 1)})
    grid = tuple(MomentumStrategy(MomentumParams(top_n=n)) for n in (3, 2, 1))
    assert neighbours(grid, 0, "S1") == [grid[1]]


@pytest.mark.parametrize(("gross_pnl", "share"), [(D(50), "10.0000"), (D(0), None), (D(-1), None)])
def test_combined_costs_hand_calculation(gross_pnl: Decimal, share: str | None) -> None:
    from types import SimpleNamespace
    from typing import cast

    from aitrader.backtest import BacktestResult
    from aitrader.backtest.walkforward import combined_costs

    pnl = SimpleNamespace(
        fees_by_type_usd={"commission": D(3)}, fees_usd=D(3), trading_pnl_usd=gross_pnl - 5
    )
    ledger = SimpleNamespace(positions={}, trading_pnl=lambda *_: pnl)
    result = cast(
        BacktestResult,
        SimpleNamespace(ledger=ledger, points=[None] * 126, spread_and_slippage_usd=D(2)),
    )
    costs = combined_costs(
        [result, result], settings(), DailyBarSource(Path("tests/fixtures/bars"), CAL)
    )
    assert costs == {
        "fees_usd": {"commission": "6.00000000"},
        "spread_and_slippage_usd": "4.00000000",
        "fx_usd": "0.00000000",
        "total_usd": "10.00000000",
        "share_of_gross_pnl_pct": share,
        "annual_pct_of_start_equity": "3.0960",
    }
