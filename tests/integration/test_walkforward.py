"""Synthetic walk-forward windows, training selection, chaining and reproducibility."""

import json
import subprocess
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import cast

import pytest
import yaml

from aitrader.backtest import build_manifest, metrics_for, run_backtest, write_results
from aitrader.backtest.walkforward import (
    Fold,
    add_months,
    chain_results,
    choose,
    make_folds,
    run_walkforward,
    variants,
    write_walkforward,
)
from aitrader.data import DailyBarRow, DailyBarSource, DataError, write_daily_csv
from aitrader.domain import OrderIntent
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import EquityPoint, buy_and_hold
from aitrader.strategy import MomentumStrategy, StrategyContext, params_hash
from tests.integration.test_backtest import CAL, cli, settings

D = Decimal


def synthetic(root: Path) -> tuple[DailyBarSource, DailyBarSource]:
    days = CAL.sessions(date(2021, 1, 4), date(2022, 8, 31))
    for index, symbol in enumerate(("SPY", "IVV", "QQQ", "IWM", "GLD")):
        rows = []
        for i, day in enumerate(days):
            price = D(90 + index * 5) + D(i) / 30 + D(i % 7) / 50
            rows.append(
                DailyBarRow(
                    session=day,
                    open=price,
                    close=price,
                    high=price + D("0.2"),
                    low=price - D("0.2"),
                    volume=D(1_000_000),
                )
            )
        for directory in ("raw", "adjusted"):
            write_daily_csv(root / directory, symbol, rows)
    return DailyBarSource(root / "raw", CAL), DailyBarSource(root / "adjusted", CAL)


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2023, 1, 31), 1, date(2023, 2, 28)),
        (date(2024, 1, 31), 1, date(2024, 2, 29)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),
        (date(2024, 3, 31), -1, date(2024, 2, 29)),
        (date(2023, 12, 31), 2, date(2024, 2, 29)),
    ],
)
def test_month_arithmetic(day: date, months: int, expected: date) -> None:
    assert add_months(day, months) == expected


def test_fold_list_and_holdout_boundary() -> None:
    folds = make_folds(date(2021, 1, 31), date(2021, 6, 30), 2, 1)
    assert folds == [
        Fold(date(2021, 1, 31), date(2021, 3, 31), date(2021, 4, 30)),
        Fold(date(2021, 2, 28), date(2021, 4, 30), date(2021, 5, 31)),
        Fold(date(2021, 3, 31), date(2021, 5, 31), date(2021, 6, 30)),
    ]
    assert all(f.end <= date(2021, 6, 30) for f in folds)
    for train, test in ((0, 1), (1, 0), (-1, 1)):
        with pytest.raises(DataError, match="must be positive"):
            make_folds(date(2021, 1, 1), date(2022, 1, 1), train, test)
    with pytest.raises(DataError, match=r"no fold fits.*2021-01-01.*2021-02-01"):
        make_folds(date(2021, 1, 1), date(2021, 2, 1), 2, 1)


def test_selection_known_scores_and_grid() -> None:
    def metric(sharpe: str | None, total: str) -> dict[str, object]:
        return {"risk": {"sharpe_rf0": sharpe}, "returns": {"total_pct": total}}

    assert choose([metric("1", "100"), metric("2", "1")]) == 1
    assert choose([metric("2", "1"), metric("2", "2")]) == 1
    assert choose([metric("2", "1"), metric("2", "1")]) == 0
    assert choose([metric(None, "100"), metric("-99", "-1")]) == 1
    assert choose([metric(None, "1"), metric(None, "2")]) == 1
    assert [(v.params()["top_n"], v.params()["skip"]) for v in variants("S1")] == [
        ("3", "0"),
        ("3", "5"),
        ("2", "0"),
        ("2", "5"),
    ]
    assert len(variants("S2")) == 1
    with pytest.raises(DataError, match="unknown"):
        variants("S3")


def test_s1_end_to_end(tmp_path: Path) -> None:
    raw, adjusted = synthetic(tmp_path)
    for name in ("a", "b"):
        strategy = MomentumStrategy()
        result = run_backtest(
            settings(),
            strategy,
            raw,
            adjusted=adjusted,
            start=date(2021, 1, 4),
            end=date(2022, 8, 31),
            calendar=CAL,
        )
        assert len(result.points) > 400
        assert result.fills
        assert not ({"SPY", "IVV"} <= set(result.ledger.positions))
        for intent in result.intents:
            day = intent.signal_ts.date()
            assert day.isocalendar()[:2] != CAL.next_session(day).isocalendar()[:2]
        manifest = build_manifest(
            settings=settings(),
            strategy=strategy,
            source=raw,
            adjusted=adjusted,
            start=date(2021, 1, 4),
            end=date(2022, 8, 31),
            git_commit="test",
            variants_tried=1,
        )
        write_results(tmp_path / name, manifest, metrics_for(result, raw, settings()), result)
    for filename in ("manifest.yaml", "metrics.json", "equity.csv"):
        assert (tmp_path / "a" / filename).read_bytes() == (tmp_path / "b" / filename).read_bytes()


def test_walkforward_e2e_chain_and_determinism(tmp_path: Path) -> None:
    raw, adjusted = synthetic(tmp_path)
    before = perf_counter()
    manifest, report = run_walkforward(
        settings(),
        "S1",
        raw,
        adjusted=adjusted,
        start=date(2021, 10, 2),
        train_months=2,
        test_months=1,
        holdout_months=2,
        calendar=CAL,
    )
    print(f"synthetic walk-forward seconds: {perf_counter() - before:.3f}")
    assert manifest["holdout_start"] == "2022-07-01"
    assert manifest["data_end"] == "2022-08-31"
    assert manifest["holdout_used"] is False
    assert manifest["variants_tried"] == 4
    folds = cast(list[dict[str, object]], report["folds"])
    expected = D(323)
    sessions: list[str] = []
    expected_curve: list[Decimal] = []
    for fold in folds:
        period = cast(dict[str, str], fold["test"])
        first, end = (
            date.fromisoformat(period["start"]),
            date.fromisoformat(period["end_exclusive"]),
        )
        assert end <= date(2022, 7, 1)
        chosen = next(v for v in variants("S1") if params_hash(v) == fold["selected_variant"])
        result = run_backtest(
            settings(),
            chosen,
            raw,
            adjusted=adjusted,
            start=first,
            end=CAL.previous_session(end),
            calendar=CAL,
        )
        base = expected
        sessions.extend(p.session.isoformat() for p in result.points)
        expected_curve.extend(base * p.equity_usd / 323 for p in result.points)
        expected = expected_curve[-1]
    equity = cast(list[dict[str, str]], report["equity"])
    assert [p["session"] for p in equity] == sessions
    assert [D(p["equity_usd"]) for p in equity] == expected_curve
    benchmark = buy_and_hold(
        [b for b in raw.bars("SPY") if b.ts.date().isoformat() in sessions],
        D(323),
        WEBULL_JP,
        ExecutionAssumptions(),
    )
    combined = cast(dict[str, object], report["combined"])
    benchmarks = cast(dict[str, dict[str, str]], combined["benchmarks"])
    assert benchmarks["B0_buy_and_hold"]["total_pct"] == format(
        ((benchmark[-1].equity_usd / 323 - 1) * 100).quantize(D("0.0001")), "f"
    )
    assert all(p["session"] < manifest["holdout_start"] for p in equity)
    write_walkforward(tmp_path / "a", manifest, report)
    second_manifest, second = run_walkforward(
        settings(),
        "S1",
        raw,
        adjusted=adjusted,
        start=date(2021, 10, 2),
        train_months=2,
        test_months=1,
        holdout_months=2,
    )
    write_walkforward(tmp_path / "b", second_manifest, second)
    for filename in ("manifest.yaml", "walkforward.json"):
        assert (tmp_path / "a" / filename).read_bytes() == (tmp_path / "b" / filename).read_bytes()
    assert yaml.safe_load((tmp_path / "a/manifest.yaml").read_text())["variants_tried"] == 4
    assert "combined" in json.loads((tmp_path / "a/walkforward.json").read_text())


def test_chain_hand_calculation() -> None:
    from types import SimpleNamespace

    from aitrader.backtest import BacktestResult

    results = cast(
        list[BacktestResult],
        [
            SimpleNamespace(
                start_equity_usd=D(100),
                points=[
                    EquityPoint(date(2023, 1, 3), D(110), D(55)),
                    EquityPoint(date(2023, 1, 4), D(99), D(0)),
                ],
            ),
            SimpleNamespace(
                start_equity_usd=D(100), points=[EquityPoint(date(2023, 1, 5), D(120), D(100))]
            ),
        ],
    )
    assert [p.equity_usd for p in chain_results(results, D(100))] == [D(110), D(99), D("118.8")]


@pytest.mark.parametrize("strategy", ["S1", "S2"])
def test_cli_walkforward(tmp_path: Path, strategy: str) -> None:
    synthetic(tmp_path)
    result = cli(
        "walkforward",
        "--data-dir",
        str(tmp_path),
        "--out-dir",
        str(tmp_path / "out"),
        "--strategy",
        strategy,
        "--start",
        "2021-10-04",
        "--train-months",
        "2",
        "--test-months",
        "1",
        "--holdout-months",
        "2",
    )
    assert result.exit_code == 0, result.output
    manifest = yaml.safe_load((tmp_path / "out/manifest.yaml").read_text())
    assert manifest["git_commit"] != "unknown"


def test_cli_errors(tmp_path: Path) -> None:
    base = ("walkforward", "--data-dir", "tests/fixtures/bars", "--out-dir", str(tmp_path / "out"))
    result = cli(*base, "--start", "2024-01-03")
    assert result.exit_code == 4
    assert "no fold fits" in result.output
    assert cli(*base, "--strategy", "NO").exit_code == 5
    assert cli(*base, "--config-dir", str(tmp_path)).exit_code == 2
    assert not (tmp_path / "out").exists()
    source = DailyBarSource(Path("tests/fixtures/bars"), CAL)
    with pytest.raises(DataError, match="holdout_months"):
        run_walkforward(settings(), "S1", source, holdout_months=0)
    no_spy = DailyBarSource(Path("tests/fixtures/bars"), CAL, symbols=["QQQ"])
    with pytest.raises(DataError, match="SPY"):
        run_walkforward(settings(), "S1", no_spy)


@pytest.mark.parametrize("failure", ["command", "os"])
def test_git_commit_failure_is_data_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from aitrader.cli.main import _git_commit

    def fail(*args: object, **kwargs: object) -> str:
        if failure == "os":
            raise OSError("unavailable")
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr("aitrader.cli.main.subprocess.check_output", fail)
    with pytest.raises(DataError, match="git"):
        _git_commit()


@pytest.mark.parametrize(
    ("scores", "totals", "expected"),
    [
        (("1", "3", "2"), ("100", "0", "2"), 1),
        (("2", "2", "1"), ("1", "2", "100"), 1),
        (("2", "2", "1"), ("2", "2", "100"), 0),
        ((None, "-10", None), ("100", "-100", "200"), 1),
    ],
)
def test_runner_selects_stub_variants_on_training_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scores: tuple[str | None, ...],
    totals: tuple[str, ...],
    expected: int,
) -> None:
    import aitrader.backtest.walkforward as wf
    from aitrader.backtest import BacktestResult
    from aitrader.config import Settings
    from aitrader.data import PointInTimeBars
    from aitrader.strategy import Strategy

    class Stub:
        strategy_id = "STUB"
        version = "1"

        def __init__(self, index: int) -> None:
            self.index = index

        def params(self) -> Mapping[str, str]:
            return {"variant": str(self.index)}

        def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
            return []

    grid = tuple(Stub(i) for i in range(3))
    monkeypatch.setattr(wf, "variants", lambda _: grid)
    calls: list[tuple[Strategy, date, date, PointInTimeBars]] = []
    result_variants: dict[int, int] = {}

    def run(
        config: Settings,
        variant: Strategy,
        source: DailyBarSource,
        *,
        start: date,
        end: date,
        calendar: object,
        point_in_time: PointInTimeBars,
    ) -> BacktestResult:
        result = run_backtest(
            config, variant, source, start=start, end=end, calendar=CAL, point_in_time=point_in_time
        )
        calls.append((variant, start, end, point_in_time))
        result_variants[id(result)] = grid.index(cast(Stub, variant))
        return result

    def metrics(
        result: BacktestResult, source: DailyBarSource, config: Settings
    ) -> dict[str, object]:
        index = result_variants[id(result)]
        actual = metrics_for(result, source, config)
        # Test-window scores deliberately favor a different variant.
        test = result.points[0].session >= date(2022, 5, 2)
        actual["risk"] = {"sharpe_rf0": str(-index) if test else scores[index]}
        actual["returns"] = {"total_pct": totals[index]}
        return actual

    monkeypatch.setattr(wf, "run_backtest", run)
    monkeypatch.setattr(wf, "metrics_for", metrics)
    raw, adjusted = synthetic(tmp_path)
    _, report = wf.run_walkforward(
        settings(),
        "S1",
        raw,
        adjusted=adjusted,
        start=date(2022, 3, 1),
        train_months=2,
        test_months=1,
        holdout_months=2,
        calendar=CAL,
    )
    folds = cast(list[dict[str, object]], report["folds"])
    assert len(folds) == 2
    assert all(f["selected_variant"] == params_hash(grid[expected]) for f in folds)
    assert all(calls[i][0] is grid[expected] for i in (3, 7))
    assert len({id(c[3]) for c in calls}) == 1
    assert all(c[2] < date(2022, 7, 1) for c in calls)
