"""Predeclared training-only selection with a locked calendar-month hold-out."""

import calendar as month_calendar
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import yaml

from aitrader.backtest.engine import BacktestResult, run_backtest
from aitrader.backtest.manifest import BENCHMARK, build_manifest, metrics_for
from aitrader.clock import TradingCalendar
from aitrader.config import Settings
from aitrader.data import CorporateActionKind, DailyBarSource, DataError, PointInTimeBars
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import EquityPoint, buy_and_hold, summarize
from aitrader.portfolio import Ledger
from aitrader.strategy import (
    MomentumParams,
    MomentumStrategy,
    PullbackStrategy,
    Strategy,
    params_hash,
)

S1_GRID = ((3, 0), (3, 5), (2, 0), (2, 5))


def variants(strategy_id: str) -> tuple[Strategy, ...]:
    if strategy_id == "S1":
        return tuple(MomentumStrategy(MomentumParams(top_n=n, skip=k)) for n, k in S1_GRID)
    if strategy_id == "S2":
        return (PullbackStrategy(),)
    raise DataError(f"unknown walk-forward strategy {strategy_id!r}")


def add_months(day: date, months: int) -> date:
    """Clamp month ends, including leap days, without a dateutil dependency."""
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    month += 1
    return date(year, month, min(day.day, month_calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Fold:
    start: date
    test_start: date
    end: date

    def periods(self) -> dict[str, dict[str, str]]:
        return {
            "train": {
                "start": self.start.isoformat(),
                "end_exclusive": self.test_start.isoformat(),
            },
            "test": {"start": self.test_start.isoformat(), "end_exclusive": self.end.isoformat()},
        }


def make_folds(start: date, holdout_start: date, train_months: int, test_months: int) -> list[Fold]:
    if train_months < 1 or test_months < 1:
        raise DataError("train_months and test_months must be positive")
    folds = []
    index = 0
    while True:
        first = add_months(start, index * test_months)
        test_start = add_months(start, index * test_months + train_months)
        end = add_months(start, index * test_months + train_months + test_months)
        if end > holdout_start:
            break
        folds.append(Fold(first, test_start, end))
        index += 1
    if not folds:
        raise DataError(
            f"no fold fits: start={start}, holdout_start={holdout_start}, "
            f"train_months={train_months}, test_months={test_months}"
        )
    return folds


def section(metrics: dict[str, object], key: str) -> dict[str, str | None]:
    return cast(dict[str, str | None], metrics[key])


def choose(training: Sequence[dict[str, object]]) -> int:
    """Stable max preserves declared grid order on exact ties; None is below any number."""

    def rank(index: int) -> tuple[bool, Decimal, Decimal]:
        score = section(training[index], "risk")["sharpe_rf0"]
        total = section(training[index], "returns")["total_pct"]
        return score is not None, Decimal(score or 0), Decimal(total or 0)

    return max(range(len(training)), key=rank)


def chain_results(results: Sequence[BacktestResult], capital: Decimal) -> list[EquityPoint]:
    """Reset each fold's denominator while carrying forward the combined capital."""
    chained = []
    equity = capital
    for result in results:
        base = equity
        for point in result.points:
            chained.append(
                EquityPoint(
                    point.session,
                    base * point.equity_usd / result.start_equity_usd,
                    base * point.market_value_usd / result.start_equity_usd,
                )
            )
        equity = chained[-1].equity_usd
    return chained


def run_walkforward(
    settings: Settings,
    strategy_id: str,
    source: DailyBarSource,
    *,
    adjusted: DailyBarSource | None = None,
    start: date = date(2017, 1, 3),
    train_months: int = 36,
    test_months: int = 6,
    holdout_months: int = 24,
    git_commit: str = "unknown",
    calendar: TradingCalendar | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    calendar = calendar or TradingCalendar()
    if holdout_months < 1:
        raise DataError("holdout_months must be positive")
    bars = source.bars(BENCHMARK)
    if not bars:
        raise DataError("SPY benchmark has no sessions")
    data_end = bars[-1].ts.date()
    holdout_start = calendar.next_session(add_months(data_end, -holdout_months))
    first = start if calendar.is_session(start) else calendar.next_session(start)
    folds = make_folds(first, holdout_start, train_months, test_months)
    grid = variants(strategy_id)
    pit = PointInTimeBars(source, adjusted)
    reports: list[dict[str, object]] = []
    tests = []
    for fold in folds:

        def run(variant: Strategy, first: date, end: date) -> BacktestResult:
            return run_backtest(
                settings,
                variant,
                source,
                start=first,
                end=end - timedelta(days=1),
                calendar=calendar,
                point_in_time=pit,
            )

        training = [
            metrics_for(run(v, fold.start, fold.test_start), source, settings) for v in grid
        ]
        selected = choose(training)
        result = run(grid[selected], fold.test_start, fold.end)
        tests.append(result)
        test_metrics = metrics_for(result, source, settings)
        reports.append(
            {
                **fold.periods(),
                "training": [
                    {
                        "params_hash": params_hash(v),
                        "sharpe_rf0": section(m, "risk")["sharpe_rf0"],
                        "total_pct": section(m, "returns")["total_pct"],
                    }
                    for v, m in zip(grid, training, strict=True)
                ],
                "selected_variant": params_hash(grid[selected]),
                "test_metrics": {k: test_metrics[k] for k in ("returns", "risk", "trades")},
            }
        )
    bt = settings.require_backtest()
    points = chain_results(tests, bt.initial_capital_usd)
    sessions = {p.session for p in points}
    benchmark = buy_and_hold(
        [b for b in bars if b.ts.date() in sessions],
        bt.initial_capital_usd,
        WEBULL_JP,
        ExecutionAssumptions(),
        distributions={
            a.session: a.cash_per_share
            for a in pit.actions(BENCHMARK)
            if a.kind is CorporateActionKind.DISTRIBUTION
        },
        dividend_us_withholding_pct=bt.dividend_us_withholding_pct,
        dividend_jp_withholding_pct=bt.dividend_jp_withholding_pct,
    )
    combined = summarize(
        start_equity=bt.initial_capital_usd,
        points=points,
        ledger=Ledger(),
        fx_jpy_per_usd=bt.fx_jpy_per_usd,
        traded_notional_usd=Decimal(0),
        spread_and_slippage_usd=Decimal(0),
        prices={},
        benchmark_symbol=BENCHMARK,
        benchmark=benchmark,
    )
    manifest = build_manifest(
        settings=settings,
        strategy=grid[0],
        source=source,
        start=first,
        end=points[-1].session,
        git_commit=git_commit,
        variants_tried=len(grid),
        point_in_time=pit,
    )
    manifest.pop("run_id")  # Backtest identity does not cover the walk-forward protocol.
    manifest.update(
        {
            "grid": [{"params_hash": params_hash(v), "params": dict(v.params())} for v in grid],
            "variants_tried": len(grid),
            "train_months": train_months,
            "test_months": test_months,
            "holdout_months": holdout_months,
            "data_end": data_end.isoformat(),
            "holdout_start": holdout_start.isoformat(),
            "holdout_used": False,
        }
    )
    return manifest, {
        "folds": reports,
        "combined": {k: combined[k] for k in ("returns", "risk", "benchmarks")},
        "equity": [
            {"session": p.session.isoformat(), "equity_usd": str(p.equity_usd)} for p in points
        ],
    }


def write_walkforward(
    out_dir: Path, manifest: dict[str, object], result: dict[str, object]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
    )
    (out_dir / "walkforward.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
