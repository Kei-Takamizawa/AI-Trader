"""Offline PAPER replay, persisted evidence, CLI failures and immutable reports."""

import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from aitrader.app import paper as runner
from aitrader.app.paper import run_paper
from aitrader.app.paper_reports import atomic_write
from aitrader.backtest import BacktestResult, run_backtest
from aitrader.backtest.snapshots import canonical_json, digest
from aitrader.cli.main import app
from aitrader.clock import SimClock, TradingCalendar
from aitrader.config import (
    ConfigError,
    PaperSettings,
    Settings,
    SimulationSettings,
    load_settings,
)
from aitrader.data import DailyBarSource
from aitrader.domain import BrokerEnvironment, Mode
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import buy_and_hold, max_drawdown
from aitrader.storage import SCHEMA, Database, InstanceLockedError, SingleInstanceLock
from aitrader.strategy import STRATEGIES, MomentumParams, MomentumStrategy

D = Decimal
REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests/fixtures/bars"
CAL = TradingCalendar()
START = date(2023, 10, 2)
FIRST_END = date(2023, 10, 23)
END = date(2023, 10, 30)
CLOCK = SimClock(datetime(2025, 1, 2, 20, tzinfo=UTC))


def settings(start: date = START) -> Settings:
    return load_settings(Mode.PAPER, REPO / "config", environ={}).model_copy(
        update={"paper": PaperSettings(start=start)}
    )


@pytest.fixture(scope="module")
def source() -> DailyBarSource:
    return DailyBarSource(FIXTURES, CAL)


@pytest.fixture(scope="module")
def replay(source: DailyBarSource) -> BacktestResult:
    return run_backtest(
        settings(),
        MomentumStrategy(),
        source,
        start=START,
        end=END,
        simulation=PaperSettings(start=START).simulation(),
        capture_sessions=True,
    )


def run(state_dir: Path, source: DailyBarSource, through: date | None = END) -> runner.PaperRun:
    return run_paper(settings(), source, None, state_dir, through, CLOCK, "fixture-sha")


def rows(state_dir: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(state_dir / "paper.db")) as conn:
        return conn.execute("SELECT * FROM paper_sessions ORDER BY session").fetchall()


def reports(state_dir: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (state_dir / "reports/daily").glob("*")
    }


def test_first_run_parity_and_status(
    tmp_path: Path, source: DailyBarSource, replay: BacktestResult
) -> None:
    result = run(tmp_path, source)
    assert result.exit_code == 0
    assert result.sessions_added == len(CAL.sessions(START, END))
    recorded = rows(tmp_path)
    assert [row[1] for row in recorded] == [s.snapshot_json for s in replay.sessions]
    assert [row[2] for row in recorded] == [s.snapshot_hash for s in replay.sessions]
    assert all(
        row[3:] == (CLOCK.now().isoformat(), "fixture-sha", source.snapshot_hash)
        for row in recorded
    )
    with Database(tmp_path / "paper.db") as db:
        book = db.query("SELECT * FROM paper_book")[0]
        assert book["start"] == START.isoformat()
        assert book["strategy_id"] == "S1"
        assert book["strategy_version"] == "1.0.0"
        assert book["risk_hash"] == digest(settings().risk.model_dump())
        assert book["simulation"] == canonical_json(
            PaperSettings(start=START).simulation().model_dump()
        )
    status = result.status
    assert status.state == "RUNNING"
    assert status.sessions_recorded == len(replay.points)
    assert status.last_session == END
    assert status.data_end == date(2024, 12, 31)
    assert not status.data_stale  # Before the 2 January close, 31 December is current.
    assert status.as_of == CLOCK.now()
    assert status.start == START
    assert status.git_commit == "fixture-sha"
    assert status.equity_usd == replay.points[-1].equity_usd
    assert status.equity_jpy == replay.points[-1].equity_usd * 155
    assert status.return_since_start == replay.points[-1].equity_usd / 323 - 1
    assert status.max_drawdown_since_start == max_drawdown(D(323), replay.points)[0]
    assert status.positions == replay.sessions[-1].positions
    assert status.next_intents == replay.sessions[-1].next_intents
    b0 = buy_and_hold(
        [b for b in source.bars("SPY") if START <= b.ts.date() <= END],
        D(323),
        WEBULL_JP,
        ExecutionAssumptions(),
    )
    assert status.b0_return_since_start == b0[-1].equity_usd / 323 - 1
    assert json.loads((tmp_path / "status/latest.json").read_text())["state"] == "RUNNING"
    assert len(reports(tmp_path)) == 2 * len(replay.sessions)
    for snapshot, point in zip(replay.sessions, replay.points, strict=True):
        assert snapshot.equity_usd == point.equity_usd
        assert (
            snapshot.cash_usd_settled
            + snapshot.cash_usd_unsettled
            + sum((p.market_value_usd for p in snapshot.positions), D(0))
            == point.equity_usd
        )
    # A fill-bearing close contains actual ledger fees, rather than recalculated estimates.
    snapshot = next(s for s in replay.sessions if s.fills)
    report = json.loads((tmp_path / f"reports/daily/{snapshot.session}.json").read_text())
    assert report["snapshot_hash"] == snapshot.snapshot_hash
    assert D(report["fees_usd"]) == snapshot.fees_usd > 0
    text = (tmp_path / f"reports/daily/{snapshot.session}.md").read_text()
    assert "BUY" in text
    assert "fees USD" in text
    assert "SPY" in text
    previous = replay.sessions[replay.sessions.index(snapshot) - 1].equity_usd
    assert D(report["day_pnl_usd"]) == snapshot.equity_usd - previous
    assert any(s.cash_usd_unsettled > 0 for s in replay.sessions)


def test_idempotence_and_catchup_then_older_through(tmp_path: Path, source: DailyBarSource) -> None:
    first = run(tmp_path, source, FIRST_END)
    before, files = rows(tmp_path), reports(tmp_path)
    second = run(tmp_path, source, FIRST_END)
    assert second.exit_code == 0
    assert second.sessions_added == 0
    assert rows(tmp_path) == before
    assert reports(tmp_path) == files
    later = run(tmp_path, source)
    assert later.sessions_added == len(CAL.sessions(CAL.next_session(FIRST_END), END))
    assert rows(tmp_path)[: first.sessions_added] == before
    assert all(reports(tmp_path)[name] == content for name, content in files.items())
    all_rows, all_files = rows(tmp_path), reports(tmp_path)
    older = run(tmp_path, source, FIRST_END)
    assert older.sessions_added == 0
    assert older.status.last_session == END
    assert rows(tmp_path) == all_rows
    assert reports(tmp_path) == all_files


@pytest.mark.parametrize("through", [FIRST_END, END])
def test_missing_daily_reports_recovered_without_inserts(
    tmp_path: Path, source: DailyBarSource, through: date
) -> None:
    assert run(tmp_path, source).exit_code == 0
    before, files = rows(tmp_path), reports(tmp_path)
    missing = {f"{START}.json", f"{FIRST_END}.md", f"{END}.json"}
    for name in missing:
        (tmp_path / "reports/daily" / name).unlink()

    result = run(tmp_path, source, through)
    assert result.exit_code == 0
    assert result.status.state == "RUNNING"
    assert result.sessions_added == 0
    assert rows(tmp_path) == before
    actual = reports(tmp_path)
    for name, original in files.items():
        if name in missing:
            if date.fromisoformat(Path(name).stem) <= through:
                assert actual[name][0] == original[0]
            else:
                assert name not in actual
        else:
            assert actual[name] == original


@pytest.mark.parametrize("suffix", [".json", ".md"])
def test_report_failure_after_commit_halts_and_next_cli_run_recovers(
    tmp_path: Path, source: DailyBarSource, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    cfg, state = make_config(tmp_path), tmp_path / "state"
    reference = tmp_path / "reference"
    assert run(reference, source).exit_code == 0
    expected = reports(reference)
    assert cli(cfg, state, "--through", FIRST_END.isoformat()).exit_code == 0
    preserved = reports(state)
    failed_session = CAL.next_session(FIRST_END)
    failed_path = state / f"reports/daily/{failed_session}{suffix}"

    def fail_report(path: Path, content: str) -> None:
        if path == failed_path:
            raise OSError("simulated daily report failure")
        atomic_write(path, content)

    with monkeypatch.context() as patch:
        patch.setattr("aitrader.app.paper_reports.atomic_write", fail_report)
        failed = cli(cfg, state, "--through", END.isoformat())
    assert failed.exit_code == 4
    assert "HALTED" in failed.output
    status = json.loads((state / "status/latest.json").read_text())
    assert status["state"] == "HALTED"
    assert status["reason"] == f"daily report {failed_session}: simulated daily report failure"
    committed = rows(state)
    assert len(committed) == len(CAL.sessions(START, END))
    assert not failed_path.exists()
    before_recovery = reports(state)
    assert all(before_recovery[name] == value for name, value in preserved.items())
    assert (f"{failed_session}.json" in before_recovery) == (suffix == ".md")

    recovered = cli(cfg, state, "--through", END.isoformat())
    assert recovered.exit_code == 0
    assert "RUNNING" in recovered.output
    assert "sessions_added=0" in recovered.output
    assert rows(state) == committed
    actual = reports(state)
    assert {name: value[0] for name, value in actual.items()} == {
        name: value[0] for name, value in expected.items()
    }
    assert all(actual[name] == value for name, value in before_recovery.items())
    assert json.loads((state / "status/latest.json").read_text())["state"] == "RUNNING"


@pytest.mark.parametrize("tamper", ["hash", "equity", "json", "array", "rehashed"])
def test_tampered_session_halts_without_any_writes(
    tmp_path: Path, source: DailyBarSource, tamper: str
) -> None:
    run(tmp_path, source, FIRST_END)
    with Database(tmp_path / "paper.db") as db, db.transaction() as conn:
        if tamper == "hash":
            conn.execute(
                "UPDATE paper_sessions SET snapshot_hash = 'bad' WHERE session = ?",
                (FIRST_END.isoformat(),),
            )
        else:
            snapshot = json.loads(
                db.query(
                    "SELECT snapshot_json FROM paper_sessions WHERE session = ?",
                    (FIRST_END.isoformat(),),
                )[0][0]
            )
            snapshot["equity_usd"] = "999"
            payload = {"json": "{broken", "array": "[]"}.get(tamper, canonical_json(snapshot))
            conn.execute(
                "UPDATE paper_sessions SET snapshot_json = ? WHERE session = ?",
                (payload, FIRST_END.isoformat()),
            )
            if tamper == "rehashed":
                conn.execute(
                    "UPDATE paper_sessions SET snapshot_hash = ? WHERE session = ?",
                    (digest(snapshot), FIRST_END.isoformat()),
                )
    before, files = rows(tmp_path), reports(tmp_path)
    result = run(tmp_path, source)
    assert result.exit_code == 6
    assert result.status.state == "HALTED"
    assert FIRST_END.isoformat() in result.status.reason
    assert (
        "equity_usd" if tamper in ("equity", "rehashed") else "snapshot_"
    ) in result.status.reason
    assert rows(tmp_path) == before
    assert reports(tmp_path) == files
    assert result.sessions_added == 0


@pytest.mark.parametrize("change", ["start", "simulation", "risk", "params", "version", "id"])
def test_changed_book_needs_new_directory(
    tmp_path: Path, source: DailyBarSource, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    run(tmp_path, source, FIRST_END)
    cfg = settings()
    if change == "start":
        cfg = settings(date(2023, 10, 3))
    elif change == "simulation":
        cfg = cfg.model_copy(update={"paper": PaperSettings(start=START, fx_jpy_per_usd=D(154))})
    elif change == "risk":
        cfg = cfg.model_copy(
            update={"risk": cfg.risk.model_copy(update={"risk_per_trade_pct": D("0.4")})}
        )
    else:
        strategy = (
            MomentumStrategy(MomentumParams(top_n=2)) if change == "params" else MomentumStrategy()
        )
        if change == "version":
            strategy.version = "test-version"
        if change == "id":
            strategy.strategy_id = "TEST"
        monkeypatch.setitem(STRATEGIES, "S1", lambda: strategy)
    before, files = rows(tmp_path), reports(tmp_path)
    result = run_paper(cfg, source, None, tmp_path, END, CLOCK, "new-sha")
    assert result.exit_code == 6
    assert result.status.state == "HALTED"
    assert "new state directory" in result.status.reason
    assert rows(tmp_path) == before
    assert reports(tmp_path) == files
    assert result.status.start == START  # Report the original book, even for a changed config.


@pytest.mark.parametrize("through", [START - timedelta(days=1), date(2023, 10, 1)])
def test_not_started(tmp_path: Path, source: DailyBarSource, through: date) -> None:
    result = run(tmp_path, source, through)
    assert result.status.state == "NOT_STARTED"
    assert result.sessions_added == 0
    assert result.exit_code == 0
    assert rows(tmp_path) == []
    assert reports(tmp_path) == {}
    assert result.status.equity_usd == 323
    assert result.status.return_since_start == 0


def test_start_on_closed_day_without_sessions(tmp_path: Path, source: DailyBarSource) -> None:
    result = run_paper(
        settings(date(2023, 10, 7)), source, None, tmp_path, date(2023, 10, 8), CLOCK, "test"
    )
    assert result.status.state == "NOT_STARTED"
    assert rows(tmp_path) == []


def test_after_data_end_refused_and_default_catches_up(
    tmp_path: Path, source: DailyBarSource
) -> None:
    result = run(tmp_path, source, date(2025, 1, 1))
    assert result.exit_code == 5
    assert "later than data_end" in result.status.reason
    assert rows(tmp_path) == []
    result = run(tmp_path, source, None)
    assert result.exit_code == 0
    assert result.status.last_session == date(2024, 12, 31)


def test_lock_refused(tmp_path: Path, source: DailyBarSource) -> None:
    with (
        SingleInstanceLock(tmp_path / "paper.db"),
        pytest.raises(InstanceLockedError, match="held"),
    ):
        run(tmp_path, source)
    assert not (tmp_path / "paper.db").exists()
    assert run(tmp_path, source).exit_code == 0


@pytest.mark.parametrize(
    ("clock_ts", "stale"),
    [
        (datetime(2024, 12, 31, 20, 59, 59, tzinfo=UTC), False),
        (datetime(2024, 12, 31, 21, tzinfo=UTC), False),
        (datetime(2025, 1, 2, 20, 59, 59, tzinfo=UTC), False),
        (datetime(2025, 1, 2, 21, tzinfo=UTC), True),
        (datetime(2025, 1, 2, 21, 0, 1, tzinfo=UTC), True),
        (datetime(2025, 1, 4, 12, tzinfo=UTC), True),
    ],
)
def test_staleness_uses_last_completed_exchange_session(
    tmp_path: Path, source: DailyBarSource, clock_ts: datetime, stale: bool
) -> None:
    result = run_paper(settings(), source, None, tmp_path, FIRST_END, SimClock(clock_ts), "test")
    assert result.status.data_stale is stale


def test_missing_spy_halts(tmp_path: Path) -> None:
    source = DailyBarSource(FIXTURES, CAL, symbols=["QQQ"])
    result = run(tmp_path, source)
    assert result.exit_code == 4
    assert "no SPY" in result.status.reason
    assert result.status.data_stale
    assert rows(tmp_path) == []


def test_changed_market_data_halts_on_first_affected_close(
    tmp_path: Path, source: DailyBarSource
) -> None:
    state = tmp_path / "state"
    run(state, source, FIRST_END)
    bars = tmp_path / "bars"
    shutil.copytree(FIXTURES, bars)
    path = bars / "SPY.csv"
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith(FIRST_END.isoformat()):
            row = line.split(",")
            lines[i] = ",".join([row[0], *(str(D(p) + 1) for p in row[1:5]), row[5]])
    path.write_text("\n".join(lines) + "\n")
    before = rows(state)
    result = run(state, DailyBarSource(bars, CAL))
    assert result.exit_code == 6
    assert FIRST_END.isoformat() in result.status.reason
    assert "equity_usd" in result.status.reason
    assert rows(state) == before


def adjusted_source(tmp_path: Path, ex: date, factor: Decimal) -> DailyBarSource:
    shutil.copytree(FIXTURES, tmp_path)
    path = tmp_path / "SPY.csv"
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines[1:], start=1):
        row = line.split(",")
        if date.fromisoformat(row[0]) < ex:
            lines[i] = ",".join([row[0], *(str(D(p) * factor) for p in row[1:5]), row[5]])
    path.write_text("\n".join(lines) + "\n")
    return DailyBarSource(tmp_path, CAL)


def test_corporate_action_on_held_position_records_nothing(
    tmp_path: Path, source: DailyBarSource
) -> None:
    state = tmp_path / "state"
    adjusted = adjusted_source(tmp_path / "adjusted", date(2023, 10, 24), D("0.5"))
    first = run_paper(settings(), source, adjusted, state, FIRST_END, CLOCK, "test")
    assert first.exit_code == 0
    assert any(p.symbol == "SPY" for p in first.status.positions)
    before, files = rows(state), reports(state)
    result = run_paper(settings(), source, adjusted, state, END, CLOCK, "test")
    assert result.exit_code == 4
    assert result.status.state == "HALTED"
    assert "held position" in result.status.reason
    assert "2023-10-24" in result.status.reason
    assert rows(state) == before
    assert reports(state) == files
    empty = tmp_path / "empty"
    failed = run_paper(settings(), source, adjusted, empty, END, CLOCK, "test")
    assert failed.exit_code == 4
    assert rows(empty) == []
    with Database(empty / "paper.db") as db:
        assert db.query("SELECT * FROM paper_book") == []


def test_distribution_uses_same_book_and_b0_tax_settings(
    tmp_path: Path, source: DailyBarSource
) -> None:
    adjusted = adjusted_source(tmp_path / "adjusted", date(2023, 10, 24), D("0.99"))
    state = tmp_path / "state"
    result = run_paper(settings(), source, adjusted, state, END, CLOCK, "test")
    assert result.exit_code == 0
    assert result.status.b0_return_since_start is not None
    plain = run(tmp_path / "plain", source)
    assert plain.status.b0_return_since_start is not None
    assert result.status.b0_return_since_start > plain.status.b0_return_since_start
    repeat = run_paper(settings(), source, adjusted, state, END, CLOCK, "test")
    assert repeat.exit_code == 0
    assert repeat.sessions_added == 0


@pytest.mark.parametrize(
    "cfg",
    [
        settings().model_copy(update={"mode": Mode.BACKTEST}),
        settings().model_copy(update={"broker_environment": BrokerEnvironment.WEBULL_SANDBOX}),
        settings().model_copy(update={"paper": None}),
    ],
)
def test_invalid_wiring_rejected_before_state_is_created(
    tmp_path: Path, source: DailyBarSource, cfg: Settings
) -> None:
    state = tmp_path / "state"
    with pytest.raises(ConfigError, match="PAPER"):
        run_paper(cfg, source, None, state, END, CLOCK, "test")
    assert not state.exists()


def test_settings_defaults_and_simulation_validation() -> None:
    bt = load_settings(Mode.BACKTEST, REPO / "config", environ={}).require_backtest()
    sim = bt.simulation()
    assert sim == SimulationSettings()
    assert sim.model_dump() == {
        "initial_capital_usd": D(323),
        "fx_jpy_per_usd": D(155),
        "half_spread_bps": D(1),
        "max_participation_pct": D(1),
        "dividend_us_withholding_pct": D(10),
        "dividend_jp_withholding_pct": D("20.315"),
    }
    with pytest.raises(ValidationError, match="virtual_capital_jpy"):
        PaperSettings.model_validate({"start": START, "virtual_capital_jpy": 50000})
    with pytest.raises(ValidationError, match="start"):
        PaperSettings.model_validate({})


@pytest.mark.parametrize("field", list(SimulationSettings.model_fields))
@pytest.mark.parametrize("value", [D(-1), D(0), D("0.001"), D(100), D("100.001"), 0.5])
def test_simulation_thresholds(field: str, value: Decimal | float) -> None:
    withholding = "withholding" in field
    valid = isinstance(value, Decimal) and ((0 <= value <= 100) if withholding else value > 0)
    if valid:
        assert getattr(SimulationSettings.model_validate({field: value}), field) == value
    else:
        with pytest.raises(ValidationError):
            SimulationSettings.model_validate({field: value})


def make_config(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    shutil.copytree(REPO / "config", config)
    path = config / "custom.yaml"
    path.write_text((config / "paper.yaml").read_text().replace("2026-10-26", START.isoformat()))
    return path


def cli(config: Path, state: Path, *args: str) -> Any:
    return CliRunner().invoke(
        app,
        [
            "paper",
            "run",
            "--config",
            str(config),
            "--data-dir",
            str(FIXTURES),
            "--state-dir",
            str(state),
            "--git-commit",
            "cli-fixture",
            *args,
        ],
    )


def test_cli_first_and_second_run_and_mismatch(tmp_path: Path) -> None:
    cfg, state = make_config(tmp_path), tmp_path / "state"
    first = cli(cfg, state, "--through", FIRST_END.isoformat())
    assert first.exit_code == 0
    assert "RUNNING sessions_added=16" in first.output
    second = cli(cfg, state, "--through", FIRST_END.isoformat())
    assert second.exit_code == 0
    assert "sessions_added=0" in second.output
    with Database(state / "paper.db") as db, db.transaction() as conn:
        conn.execute("UPDATE paper_sessions SET snapshot_hash = 'bad'")
    mismatch = cli(cfg, state, "--through", END.isoformat())
    assert mismatch.exit_code == 6
    assert "HALTED" in mismatch.output


def test_cli_errors_and_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, state = make_config(tmp_path), tmp_path / "state"
    assert cli(tmp_path / "missing.yaml", state).exit_code == 2
    assert cli(cfg, state, "--through", "invalid").exit_code == 5
    assert cli(cfg, state, "--through", "2025-01-01").exit_code == 5
    with SingleInstanceLock(state / "paper.db"):
        result = cli(cfg, state)
        assert result.exit_code == 5
        assert "held" in result.output
    missing = cli(cfg, state, "--data-dir", str(tmp_path / "missing"))
    assert missing.exit_code == 4
    assert "HALTED" in missing.output
    assert json.loads((state / "status/latest.json").read_text())["reason"].startswith("no CSV")
    # Also exercise the explicit provenance failure, without calling Git or the network.
    monkeypatch.setattr("aitrader.cli.main._git_commit", lambda *args: fail_commit())
    assert cli(cfg, state).exit_code == 4


def fail_commit() -> str:
    from aitrader.data import DataError

    raise DataError("cannot read the git commit")


def test_atomic_file_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "latest.json"
    atomic_write(target, "old")
    replace = Path.replace
    seen: list[str] = []

    def observe(path: Path, destination: Path) -> Path:
        assert target.read_text() == "old"
        assert path.read_text() == "new"
        assert path.parent == target.parent
        seen.append("complete")
        return replace(path, destination)

    monkeypatch.setattr(Path, "replace", observe)
    atomic_write(target, "new")
    assert seen == ["complete"]
    assert target.read_text() == "new"
    assert list(tmp_path.iterdir()) == [target]


def test_schema_two_is_upgraded_without_changing_old_tables(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (2)")
        for step in SCHEMA[:2]:
            for sql in step:
                conn.execute(sql)
        conn.execute(
            "INSERT INTO runs VALUES ('old', 'backtest', 'sha', 'hash', '2023-01-03', NULL)"
        )
    with Database(path) as db:
        assert db.schema_version == 3
        assert db.query("SELECT run_id FROM runs")[0][0] == "old"
        assert db.query("SELECT * FROM paper_book") == []
        assert db.query("SELECT * FROM paper_sessions") == []


def test_cli_and_runner_import_without_webull(tmp_path: Path) -> None:
    code = """
import importlib.abc
import sys
class RefuseWebull(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(('aitrader.broker.webull', 'webull')):
            raise RuntimeError('PAPER imported Webull')
sys.meta_path.insert(0, RefuseWebull())
from aitrader.cli.main import app
from aitrader.app.paper import run_paper
from aitrader.config import load_settings, PaperSettings
from aitrader.domain import Mode
from aitrader.clock import SimClock, TradingCalendar
from aitrader.data import DailyBarSource
from pathlib import Path
from datetime import date, datetime, UTC
settings = load_settings(Mode.PAPER, Path('config'), environ={}).model_copy(
    update={'paper': PaperSettings(start=date(2023, 10, 2))})
result = run_paper(settings, DailyBarSource(Path('tests/fixtures/bars'), TradingCalendar()),
    None, Path(sys.argv[1]), date(2023, 10, 3),
    SimClock(datetime(2024, 1, 1, tzinfo=UTC)), 'test')
assert result.exit_code == 0
assert not any(n.startswith(('aitrader.broker.webull', 'webull')) for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_preexisting_reports_are_preserved(tmp_path: Path, source: DailyBarSource) -> None:
    target = tmp_path / f"reports/daily/{FIRST_END}.md"
    target.parent.mkdir(parents=True)
    target.write_text("existing report")
    before = target.stat().st_mtime_ns
    result = run(tmp_path, source, FIRST_END)
    assert result.exit_code == 0
    assert target.read_text() == "existing report"
    assert target.stat().st_mtime_ns == before
    assert target.with_suffix(".json").exists()


@pytest.mark.parametrize("operation", ["flush", "rename"])
def test_failed_atomic_write_leaves_old_complete_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    target = tmp_path / "latest.json"
    atomic_write(target, "old")

    def fail(*args: object) -> None:
        raise OSError("simulated disk failure")

    if operation == "flush":
        monkeypatch.setattr("aitrader.app.paper_reports.os.fsync", fail)
    else:
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="simulated disk failure"):
        atomic_write(target, "new")
    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_reports_include_rejections_pending_reasons_and_findings(
    tmp_path: Path, replay: BacktestResult
) -> None:
    from aitrader.app.paper_reports import write_daily
    from aitrader.domain import RiskDecision, RuleResult, Verdict

    snapshot = next(s for s in replay.sessions if s.next_intents)
    rejection = RiskDecision(
        intent_id=snapshot.next_intents[0].intent_id,
        verdict=Verdict.REJECT,
        approved_quantity=D(0),
        limits_hash="test",
        rule_results=(
            RuleResult(rule_id="DQ-2", passed=False, detail="missing bar"),
            RuleResult(rule_id="HC-1", passed=True, detail="within cap"),
        ),
    )
    snapshot = snapshot.model_copy(
        update={
            "rejections": (rejection,),
            "skipped": ("missing quote",),
            "data_quality": {"SPY": ("DQ-2",)},
        }
    )
    write_daily(
        tmp_path, snapshot, D(323), strategy_id="S1", strategy_version="1.0.0", purpose="reference"
    )
    before = reports(tmp_path)
    write_daily(
        tmp_path, snapshot, D(323), strategy_id="S1", strategy_version="1.0.0", purpose="reference"
    )
    assert reports(tmp_path) == before
    text = (tmp_path / f"reports/daily/{snapshot.session}.md").read_text()
    assert "DQ-2: missing bar" in text
    assert "HC-1: within cap" not in text
    assert "missing quote" in text
    for intent in snapshot.next_intents:
        assert intent.reason in text
        assert str(intent.stop_price) in text
    data = json.loads((tmp_path / f"reports/daily/{snapshot.session}.json").read_text())
    assert data["data_quality"] == {"SPY": ["DQ-2"]}
    assert data["rejections"][0]["intent_id"] == rejection.intent_id


def test_new_sessions_rollback_together_on_sqlite_failure(
    tmp_path: Path, source: DailyBarSource
) -> None:
    run(tmp_path, source, FIRST_END)
    before, files = rows(tmp_path), reports(tmp_path)
    with Database(tmp_path / "paper.db") as db, db.transaction() as conn:
        conn.execute("""CREATE TRIGGER fail_second_new BEFORE INSERT ON paper_sessions
            WHEN NEW.session = '2023-10-25'
            BEGIN SELECT RAISE(ABORT, 'simulated database failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated database failure"):
        run(tmp_path, source)
    assert rows(tmp_path) == before
    assert reports(tmp_path) == files


@pytest.mark.parametrize("configured", [False, True])
def test_s1_book_matches_pre_task_fixture(
    tmp_path: Path, source: DailyBarSource, configured: bool
) -> None:
    cfg = load_settings(Mode.PAPER, REPO / "config", environ={})
    assert cfg.paper is not None
    paper = (
        cfg.paper.model_copy(update={"start": START}) if configured else PaperSettings(start=START)
    )
    assert paper.strategy == "S1"
    assert paper.purpose == "reference"
    cfg = cfg.model_copy(update={"paper": paper})
    result = run_paper(cfg, source, None, tmp_path, END, CLOCK, "fixture-sha")
    assert result.exit_code == 0
    expected = json.loads((REPO / "tests/fixtures/paper/s1-book.json").read_text())
    with Database(tmp_path / "paper.db") as db:
        assert dict(db.query("SELECT * FROM paper_book")[0]) == expected


def test_s3_config_and_fixture_parity(tmp_path: Path, source: DailyBarSource) -> None:
    cfg = load_settings(
        Mode.PAPER, REPO / "config", environ={}, config_file=REPO / "config/paper-s3.yaml"
    )
    reference = load_settings(Mode.PAPER, REPO / "config", environ={})
    assert cfg.paper is not None
    assert reference.paper is not None
    assert cfg.paper.strategy == "S3"
    assert cfg.paper.purpose == "observation"
    assert cfg.paper.start == reference.paper.start == date(2026, 10, 26)
    assert cfg.paper.simulation() == reference.paper.simulation()
    assert cfg.risk == reference.risk
    assert cfg.risk.risk_per_trade_pct == D("0.5")
    paper = cfg.paper.model_copy(update={"start": START})
    cfg = cfg.model_copy(update={"paper": paper})
    result = run_paper(cfg, source, None, tmp_path, END, CLOCK, "fixture-sha")
    assert result.exit_code == 0
    expected = run_backtest(
        cfg,
        STRATEGIES["S3"](),
        source,
        start=START,
        end=END,
        simulation=paper.simulation(),
        capture_sessions=True,
    )
    recorded = rows(tmp_path)
    assert [r[1] for r in recorded] == [s.snapshot_json for s in expected.sessions]
    assert [r[2] for r in recorded] == [s.snapshot_hash for s in expected.sessions]
    with Database(tmp_path / "paper.db") as db:
        book = db.query("SELECT * FROM paper_book")[0]
        assert book["strategy_id"] == "S3"
        assert book["strategy_version"] == "1.0.0"
    assert result.sessions_added == len(expected.sessions) > 0


@pytest.mark.parametrize("existing", ["S1", "S3"])
@pytest.mark.parametrize("missing_data", [False, True])
def test_cli_refuses_other_strategy_without_writing(
    tmp_path: Path, existing: str, missing_data: bool
) -> None:
    cfg, state = make_config(tmp_path), tmp_path / "state"
    reference = cfg.read_text()
    observation = (
        (REPO / "config/paper-s3.yaml").read_text().replace("2026-10-26", START.isoformat())
    )
    cfg.write_text(reference if existing == "S1" else observation)
    first = cli(cfg, state, "--through", FIRST_END.isoformat())
    assert first.exit_code == 0
    assert f"strategy={existing}" in first.output
    before = {str(p.relative_to(state)): p.read_bytes() for p in state.rglob("*") if p.is_file()}
    cfg.write_text(observation if existing == "S1" else reference)
    args = ["--data-dir", str(tmp_path / "missing")] if missing_data else []
    refused = cli(cfg, state, "--through", END.isoformat(), *args)
    assert refused.exit_code == 6
    assert "changed book" in refused.output
    assert "strategy_id" in refused.output
    assert "new state directory" in refused.output
    after = {str(p.relative_to(state)): p.read_bytes() for p in state.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("strategy", ["S1", "S3"])
@pytest.mark.parametrize("purpose", ["reference", "observation"])
def test_book_metadata_in_status_and_daily_reports(
    tmp_path: Path, source: DailyBarSource, strategy: str, purpose: str
) -> None:
    paper = PaperSettings.model_validate({"start": START, "strategy": strategy, "purpose": purpose})
    cfg = settings().model_copy(update={"paper": paper})
    result = run_paper(cfg, source, None, tmp_path, FIRST_END, CLOCK, "test")
    assert result.exit_code == 0
    status = json.loads((tmp_path / "status/latest.json").read_text())
    assert status["strategy_id"] == strategy
    assert status["strategy_version"] == "1.0.0"
    assert status["purpose"] == purpose
    for path in (tmp_path / "reports/daily").glob("*.md"):
        text = path.read_text()
        assert f"Strategy: {strategy} 1.0.0. Purpose: {purpose}." in text.splitlines()[:4]
        observation = (
            "Observation book: this strategy did not pass its research rules "
            "and is not a LIVE candidate."
        )
        assert (observation in text) == (purpose == "observation")
        daily = json.loads(path.with_suffix(".json").read_text())
        assert daily["strategy_id"] == strategy
        assert daily["strategy_version"] == "1.0.0"
        assert daily["purpose"] == purpose


@pytest.mark.parametrize(
    ("field", "value"), [("strategy", "S2"), ("strategy", "unknown"), ("purpose", "candidate")]
)
def test_cli_unknown_paper_setting(tmp_path: Path, field: str, value: str) -> None:
    cfg, state = make_config(tmp_path), tmp_path / "state"
    old = "S1" if field == "strategy" else "reference"
    cfg.write_text(cfg.read_text().replace(f"{field}: {old}", f"{field}: {value}"))
    result = cli(cfg, state)
    assert result.exit_code == 2
    assert f"paper.{field}" in result.output
    assert not state.exists()
