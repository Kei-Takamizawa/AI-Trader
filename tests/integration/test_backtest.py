"""End-to-end backtests on the synthetic fixture bars."""

import shutil
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from aitrader.backtest import BacktestResult, FillRecord, metrics_for, run_backtest
from aitrader.cli.main import app
from aitrader.clock import TradingCalendar
from aitrader.config import Settings, load_settings
from aitrader.data import DailyBarSource
from aitrader.domain import (
    Currency,
    IntentPurpose,
    Mode,
    Money,
    OrderIntent,
    Side,
    make_intent_id,
)
from aitrader.strategy import PullbackStrategy, StrategyContext

D = Decimal
REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "bars"
CAL = TradingCalendar()
START, END = date(2023, 1, 3), date(2024, 12, 31)


def settings() -> Settings:
    return load_settings(Mode.BACKTEST, REPO / "config")


@pytest.fixture(scope="module")
def full_run() -> BacktestResult:
    source = DailyBarSource(FIXTURES, CAL)
    return run_backtest(settings(), PullbackStrategy(), source, start=START, end=END, calendar=CAL)


def test_full_run_keeps_the_books_consistent(full_run: BacktestResult) -> None:
    result = full_run
    assert len(result.points) == len(CAL.sessions(START, END))
    ledger = result.ledger
    assert all(v == 0 for v in ledger.trial_balance().values())
    # the engine's books agree with the simulated broker's books
    broker_books = result.broker.ledger
    assert ledger.positions == broker_books.positions
    assert ledger.cash_usd_settled == broker_books.cash_usd_settled
    assert ledger.cash_usd_unsettled == broker_books.cash_usd_unsettled
    # every fill belongs to an order the engine sent, and every decision is on record
    for record in result.fills:
        assert result.store.get(record.fill.client_order_id) is not None
    assert len(result.cycle.decisions) >= len({r.fill.client_order_id for r in result.fills})
    purposes = {
        order.request.purpose
        for event in result.store.events()
        if event.event == "WRITE_AHEAD" and (order := result.store.get(event.client_order_id))
    }
    assert purposes == {IntentPurpose.ENTRY, IntentPurpose.EXIT, IntentPurpose.STOP_EXIT}
    assert result.points[-1].equity_usd == ledger.valuation({}, D(155)).equity_usd


def test_no_look_ahead_in_strategy_inputs() -> None:
    seen: list[StrategyContext] = []

    class Recorder:
        strategy_id = "REC"
        version = "1"

        def params(self) -> Mapping[str, str]:
            return {}

        def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
            seen.append(ctx)
            return []

    source = DailyBarSource(FIXTURES, CAL)
    run_backtest(
        settings(), Recorder(), source, start=date(2024, 6, 3), end=date(2024, 6, 7), calendar=CAL
    )
    assert [c.session for c in seen] == CAL.sessions(date(2024, 6, 3), date(2024, 6, 7))
    for ctx in seen:
        for bars in ctx.history.values():
            assert bars[-1].ts.date() == ctx.session  # the session just closed is included
            assert all(source.close_time(b) <= ctx.as_of for b in bars)  # nothing later


class Scripted:
    """Buys SPY once on ``day`` with a stop ``stop_pct`` below the close."""

    strategy_id = "SCRIPTED"
    version = "1"

    def __init__(self, day: date, stop_pct: str) -> None:
        self.day = day
        self.stop_pct = D(stop_pct)

    def params(self) -> Mapping[str, str]:
        return {"day": self.day.isoformat(), "stop_pct": str(self.stop_pct)}

    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
        if ctx.session != self.day:
            return []
        last = ctx.history["SPY"][-1]
        return [
            OrderIntent(
                intent_id=make_intent_id("SCRIPTED", "1", "SPY", Side.BUY, last.ts, 0),
                strategy_id="SCRIPTED",
                strategy_version="1",
                symbol="SPY",
                side=Side.BUY,
                purpose=IntentPurpose.ENTRY,
                target_notional=Money.of(80, Currency.USD),
                stop_price=(last.close * (1 - self.stop_pct / 100)).quantize(D("0.01")),
                reason="scripted",
                signal_ts=last.ts,
            )
        ]


def edited_fixtures(
    tmp_path: Path, day: date, edit: Callable[[list[str], list[str]], list[str]]
) -> Path:
    """A copy of the fixtures with SPY's row for ``day`` rewritten by ``edit(previous, row)``."""
    target = tmp_path / "bars"
    shutil.copytree(FIXTURES, target)
    path = target / "SPY.csv"
    lines = path.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith(day.isoformat()))
    lines[index] = ",".join(edit(lines[index - 1].split(","), lines[index].split(",")))
    path.write_text("\n".join(lines) + "\n")
    return target


ENTRY_SIGNAL = date(2023, 6, 1)  # bought on 2 June
STOP_DAY = date(2023, 6, 6)


def stop_exit_fills(result: BacktestResult) -> list[FillRecord]:
    def purpose(record: FillRecord) -> IntentPurpose | None:
        order = result.store.get(record.fill.client_order_id)
        return order.request.purpose if order is not None else None

    return [r for r in result.fills if purpose(r) is IntentPurpose.STOP_EXIT]


def test_a_gap_through_the_stop_sells_at_the_open(tmp_path: Path) -> None:
    def gap(previous: list[str], row: list[str]) -> list[str]:
        open_ = (D(previous[4]) * D("0.90")).quantize(D("0.01"))
        return [row[0], str(open_), str(open_ + 2), str(open_ - 1), str(open_ + 1), row[5]]

    source = DailyBarSource(edited_fixtures(tmp_path, STOP_DAY, gap), CAL)
    result = run_backtest(
        settings(),
        Scripted(ENTRY_SIGNAL, "3"),
        source,
        start=date(2023, 5, 30),
        end=date(2023, 6, 9),
        calendar=CAL,
    )
    (record,) = stop_exit_fills(result)
    fill = record.fill
    bar = source.bar_on("SPY", STOP_DAY)
    assert bar is not None
    assert fill.ts == CAL.session_open(STOP_DAY)
    # bid = open - 1 bp, minus 5 bp slippage, rounded down: well below the stop
    expected = (bar.open * (1 - D("0.0001")) * (1 - D("0.0005"))).quantize(
        D("0.01"), rounding="ROUND_FLOOR"
    )
    assert fill.price == expected
    assert result.ledger.position("SPY") is None


def test_a_stop_inside_the_day_sells_at_the_stop(tmp_path: Path) -> None:
    source = DailyBarSource(FIXTURES, CAL)
    entry_close = source.history("SPY", CAL.session_close(ENTRY_SIGNAL))[-1].close
    stop = (entry_close * D("0.97")).quantize(D("0.01"))

    def dip(previous: list[str], row: list[str]) -> list[str]:
        open_ = D(previous[4])
        return [row[0], str(open_), str(open_ + 1), str(stop - 1), str(open_), row[5]]

    edited = DailyBarSource(edited_fixtures(tmp_path, STOP_DAY, dip), CAL)
    result = run_backtest(
        settings(),
        Scripted(ENTRY_SIGNAL, "3"),
        edited,
        start=date(2023, 5, 30),
        end=date(2023, 6, 9),
        calendar=CAL,
    )
    (record,) = stop_exit_fills(result)
    fill = record.fill
    expected = (stop * (1 - D("0.0001")) * (1 - D("0.0005"))).quantize(
        D("0.01"), rounding="ROUND_FLOOR"
    )
    assert fill.price == expected
    assert fill.ts > CAL.session_open(STOP_DAY)


def test_period_without_sessions_is_refused() -> None:
    source = DailyBarSource(FIXTURES, CAL)
    with pytest.raises(ValueError, match="no sessions"):
        run_backtest(
            settings(),
            PullbackStrategy(),
            source,
            start=date(2024, 7, 4),
            end=date(2024, 7, 4),
            calendar=CAL,
        )


# ---------------------------------------------------------------- command line


RUNNER = CliRunner()


def cli(*args: str) -> Result:
    return RUNNER.invoke(app, list(args))


def test_cli_backtest_is_reproducible(tmp_path: Path) -> None:
    common = [
        "backtest",
        "--data-dir",
        str(FIXTURES),
        "--config-dir",
        str(REPO / "config"),
        "--start",
        "2023-01-03",
        "--end",
        "2024-12-31",
        "--git-commit",
        "abc123",
    ]
    first = cli(*common, "--out-dir", str(tmp_path / "a"))
    second = cli(*common, "--out-dir", str(tmp_path / "b"))
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0
    for name in ("manifest.yaml", "metrics.json", "equity.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    manifest = (tmp_path / "a" / "manifest.yaml").read_text()
    assert "git_commit: abc123" in manifest
    assert "cost_profile: webull_jp" in manifest
    assert "run_id: bt-" in first.output


def test_cli_errors(tmp_path: Path) -> None:
    unknown = cli(
        "backtest",
        "--data-dir",
        str(FIXTURES),
        "--out-dir",
        str(tmp_path),
        "--strategy",
        "NOPE",
        "--config-dir",
        str(REPO / "config"),
    )
    assert unknown.exit_code == 5
    empty = tmp_path / "empty"
    empty.mkdir()
    no_data = cli(
        "backtest",
        "--data-dir",
        str(empty),
        "--out-dir",
        str(tmp_path),
        "--config-dir",
        str(REPO / "config"),
    )
    assert no_data.exit_code == 4
    no_config = cli(
        "backtest",
        "--data-dir",
        str(FIXTURES),
        "--out-dir",
        str(tmp_path),
        "--config-dir",
        str(empty),
    )
    assert no_config.exit_code == 2


def test_metrics_value_open_positions_at_the_last_close() -> None:
    source = DailyBarSource(FIXTURES, CAL)
    result = run_backtest(
        settings(),
        Scripted(ENTRY_SIGNAL, "3"),
        source,
        start=date(2023, 5, 30),
        end=date(2023, 6, 5),
        calendar=CAL,
    )
    assert result.ledger.position("SPY") is not None
    metrics = metrics_for(result, source, settings())
    pnl = metrics["pnl"]
    assert isinstance(pnl, dict)
    assert pnl["trading_pnl_usd"] is not None
