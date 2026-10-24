"""Predeclared training-only selection with a locked calendar-month hold-out."""

import calendar as month_calendar
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import product
from pathlib import Path
from typing import cast

import yaml

from aitrader.backtest.engine import BacktestResult, run_backtest
from aitrader.backtest.manifest import BENCHMARK, build_manifest, metrics_for
from aitrader.clock import TradingCalendar
from aitrader.config import Settings
from aitrader.config.hard_caps import HARD_CAPS
from aitrader.data import CorporateActionKind, DailyBarSource, DataError, PointInTimeBars
from aitrader.domain import Mode
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import (
    EquityPoint,
    buy_and_hold,
    daily_returns,
    deflated_sharpe,
    fmt,
    sharpe_per_session,
    summarize,
)
from aitrader.portfolio import Ledger
from aitrader.strategy import (
    MomentumParams,
    MomentumStrategy,
    PullbackStrategy,
    Strategy,
    TrendCoreParams,
    TrendCoreStrategy,
    params_hash,
)

GRID_AXES: dict[str, dict[str, tuple[int, ...]]] = {
    "S1": {"top_n": (3, 2), "skip": (0, 5)},
    "S2": {},
    "S3": {"trend_period": (200, 150), "atr_stop_multiple": (3, 2)},
}
S1_GRID = tuple(product(*GRID_AXES["S1"].values()))


def variants(strategy_id: str, cash_proxy: str | None = None) -> tuple[Strategy, ...]:
    if cash_proxy is not None and strategy_id != "S1":
        raise ValueError("cash_proxy is only supported for S1")
    if strategy_id == "S1":
        return tuple(
            MomentumStrategy(MomentumParams(top_n=n, skip=k, cash_proxy=cash_proxy))
            for n, k in S1_GRID
        )
    if strategy_id == "S3":
        return tuple(
            TrendCoreStrategy(TrendCoreParams(trend_period=period, atr_stop_multiple=Decimal(stop)))
            for period, stop in product(*GRID_AXES["S3"].values())
        )
    if strategy_id == "S2":
        return (PullbackStrategy(),)
    raise DataError(f"unknown walk-forward strategy {strategy_id!r}")


def neighbours(grid: Sequence[Strategy], selected: int, strategy_id: str) -> list[Strategy]:
    """Use declared axis positions, preserving grid order for reporting."""
    axes = GRID_AXES[strategy_id]
    origin = grid[selected].params()
    adjacent = []
    for candidate in grid:
        values = candidate.params()
        changed = [key for key in axes if values.get(key) != origin.get(key)]
        if len(changed) == 1:
            key = changed[0]
            positions = [str(value) for value in axes[key]]
            if abs(positions.index(values[key]) - positions.index(origin[key])) == 1:
                adjacent.append(candidate)
    return adjacent


def same_sign(left: Decimal, right: Decimal) -> bool:
    return (left > 0) - (left < 0) == (right > 0) - (right < 0)


def combined_costs(
    results: Sequence[BacktestResult], settings: Settings, source: DailyBarSource
) -> dict[str, object]:
    """Sum independent fold books before computing ratios; do not scale costs by chaining."""
    bt = settings.require_backtest()
    fees: dict[str, Decimal] = {}
    spread = Decimal(0)
    gross = Decimal(0)
    sessions = 0
    for result in results:
        prices = {
            symbol: [
                bar for bar in source.bars(symbol) if bar.ts.date() <= result.points[-1].session
            ][-1].close
            for symbol in result.ledger.positions
        }
        pnl = result.ledger.trading_pnl(prices, bt.fx_jpy_per_usd)
        for kind, amount in pnl.fees_by_type_usd.items():
            fees[kind] = fees.get(kind, Decimal(0)) + amount
        spread += result.spread_and_slippage_usd
        gross += pnl.trading_pnl_usd + pnl.fees_usd + result.spread_and_slippage_usd
        sessions += len(result.points)
    total = sum(fees.values(), Decimal(0)) + spread
    return {
        "fees_usd": {key: fmt(value, 8) for key, value in sorted(fees.items())},
        "spread_and_slippage_usd": fmt(spread, 8),
        "fx_usd": fmt(Decimal(0), 8),
        "total_usd": fmt(total, 8),
        "share_of_gross_pnl_pct": fmt(total / gross * 100, 4) if gross > 0 else None,
        "annual_pct_of_start_equity": fmt(total / bt.initial_capital_usd * 252 / sessions * 100, 4),
    }


def validate_research_options(
    settings: Settings,
    strategy_id: str,
    risk_per_trade_pct: Decimal | None,
    cash_proxy: str | None,
    prior_variants: int,
) -> None:
    """Validate before model_copy, which deliberately does not run model validators."""
    if prior_variants < 0:
        raise ValueError("prior_variants must be nonnegative")
    if cash_proxy is not None and strategy_id != "S1":
        raise ValueError("cash_proxy is only supported for S1")
    if risk_per_trade_pct is not None:
        if settings.mode is not Mode.BACKTEST:
            raise ValueError("risk_per_trade_pct override requires BACKTEST mode")
        if not risk_per_trade_pct.is_finite() or not (
            0 < risk_per_trade_pct <= HARD_CAPS["risk_per_trade_pct"]
        ):
            raise ValueError(
                "risk_per_trade_pct must be finite and 0 < value <= "
                f"{HARD_CAPS['risk_per_trade_pct']}"
            )


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
    risk_per_trade_pct: Decimal | None = None,
    cash_proxy: str | None = None,
    prior_variants: int = 0,
) -> tuple[dict[str, object], dict[str, object]]:
    validate_research_options(settings, strategy_id, risk_per_trade_pct, cash_proxy, prior_variants)
    if risk_per_trade_pct is not None:
        settings = settings.model_copy(
            update={
                "risk": settings.risk.model_copy(update={"risk_per_trade_pct": risk_per_trade_pct})
            }
        )
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
    grid = variants(strategy_id) if cash_proxy is None else variants(strategy_id, cash_proxy)
    training_sharpes: list[Decimal] = []
    fold_summary: list[dict[str, object]] = []
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

        training = []
        for variant in grid:
            trained = run(variant, fold.start, fold.test_start)
            score = sharpe_per_session(daily_returns(trained.start_equity_usd, trained.points))
            if score is not None:
                training_sharpes.append(score)
            training.append(metrics_for(trained, source, settings))
        selected = choose(training)
        result = run(grid[selected], fold.test_start, fold.end)
        tests.append(result)
        test_metrics = metrics_for(result, source, settings)
        neighbour_reports = []
        selected_total = Decimal(str(section(test_metrics, "returns")["total_pct"]))
        for neighbour in neighbours(grid, selected, strategy_id):
            neighbour_metrics = metrics_for(
                run(neighbour, fold.test_start, fold.end), source, settings
            )
            neighbour_reports.append(
                {
                    "params": dict(neighbour.params()),
                    "params_hash": params_hash(neighbour),
                    "total_pct": section(neighbour_metrics, "returns")["total_pct"],
                }
            )
        stable = (
            all(
                same_sign(selected_total, Decimal(str(item["total_pct"])))
                for item in neighbour_reports
            )
            if neighbour_reports
            else None
        )
        fold_summary.append(
            {
                "test": fold.periods()["test"],
                "stable": stable,
                "cost_share_of_gross_pnl_pct": section(test_metrics, "costs")[
                    "share_of_gross_pnl_pct"
                ],
                "total_pct": section(test_metrics, "returns")["total_pct"],
                "profit_factor": section(test_metrics, "trades")["profit_factor"],
                "exposure_pct": section(test_metrics, "activity")["exposure_pct"],
                "selected_grid_point": {
                    key: grid[selected].params().get(key)
                    for key in (GRID_AXES[strategy_id] or ("top_n", "skip"))
                },
            }
        )
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
                "neighbours": neighbour_reports,
                "stable": stable,
                "test_metrics": {
                    k: test_metrics[k] for k in ("returns", "risk", "trades", "activity", "costs")
                },
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
    returns = daily_returns(bt.initial_capital_usd, points)
    section(combined, "risk")["sharpe_per_session"] = fmt(sharpe_per_session(returns), 4)
    section(combined, "risk")["deflated_sharpe"] = fmt(
        deflated_sharpe(returns, training_sharpes, prior_variants + len(grid)), 4
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
            "variants_tried_total": prior_variants + len(grid),
            "risk_override": (
                {"risk_per_trade_pct": str(risk_per_trade_pct)}
                if risk_per_trade_pct is not None
                else None
            ),
            "train_months": train_months,
            "test_months": test_months,
            "holdout_months": holdout_months,
            "data_end": data_end.isoformat(),
            "holdout_start": holdout_start.isoformat(),
            "holdout_used": False,
        }
    )
    combined["costs"] = combined_costs(tests, settings, source)
    combined["stability"] = {
        "stable_folds": sum(fold["stable"] is True for fold in reports),
        "folds": sum(fold["stable"] is not None for fold in reports),
    }
    return manifest, {
        "folds": reports,
        "fold_summary": fold_summary,
        "combined": {
            k: combined[k] for k in ("returns", "risk", "benchmarks", "costs", "stability")
        },
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
