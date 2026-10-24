"""Local PAPER replay: verify all recorded closes before appending any new session."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from aitrader.app.paper_reports import PaperStatus, write_daily, write_status
from aitrader.backtest import run_backtest
from aitrader.backtest.snapshots import SessionSnapshot, canonical_json, digest
from aitrader.clock import Clock, TradingCalendar
from aitrader.config import ConfigError, PaperSettings, Settings, SimulationSettings
from aitrader.data import CorporateActionKind, DailyBarSource, DataError
from aitrader.domain import BrokerEnvironment, Mode
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import EquityPoint, buy_and_hold, max_drawdown
from aitrader.storage import Database, SingleInstanceLock
from aitrader.strategy import MomentumStrategy, params_hash

EXIT_PAPER_MISMATCH = 6
EXIT_DATA_ERROR = 4
EXIT_USAGE_ERROR = 5


@dataclass(frozen=True)
class PaperRun:
    status: PaperStatus
    sessions_added: int
    exit_code: int


class PaperMismatchError(Exception):
    """A book must retain its identity and reproduce every recorded session."""


def _book(settings: Settings, paper: PaperSettings, strategy: MomentumStrategy) -> dict[str, str]:
    return {
        "start": paper.start.isoformat(),
        "strategy_id": strategy.strategy_id,
        "strategy_version": strategy.version,
        "params_hash": params_hash(strategy),
        "simulation": canonical_json(paper.simulation().model_dump()),
        "risk_hash": digest(settings.risk.model_dump()),
    }


def _verify_book(db: Database, book: dict[str, str]) -> None:
    rows = db.query("SELECT * FROM paper_book")
    if rows:
        changed = [key for key, value in book.items() if rows[0][key] != value]
        if changed:
            raise PaperMismatchError(
                f"changed book ({', '.join(changed)}) needs a new state directory; "
                "stop and report; never delete the existing state"
            )


def _verify_sessions(db: Database, snapshots: list[SessionSnapshot], through: date) -> None:
    replay = {snapshot.session.isoformat(): snapshot for snapshot in snapshots}
    for row in db.query(
        "SELECT * FROM paper_sessions WHERE session <= ? ORDER BY session", (through.isoformat(),)
    ):
        snapshot = replay.get(row["session"])
        expected = json.loads(snapshot.snapshot_json) if snapshot is not None else {}
        try:
            recorded = json.loads(row["snapshot_json"])
            if not isinstance(recorded, dict):
                raise ValueError("snapshot must be a JSON object")
            changed = sorted(
                key
                for key in recorded.keys() | expected.keys()
                if recorded.get(key) != expected.get(key)
            )
            corrupt = digest(recorded) != row["snapshot_hash"]
        except ValueError:
            changed, corrupt = ["snapshot_json"], True
        if snapshot is None or corrupt or snapshot.snapshot_hash != row["snapshot_hash"]:
            fields = ", ".join(changed) or "snapshot_hash"
            raise PaperMismatchError(f"session {row['session']} differs: {fields}")


def _recorded(db: Database) -> list[SessionSnapshot]:
    return [
        SessionSnapshot.model_validate_json(row["snapshot_json"])
        for row in db.query("SELECT snapshot_json FROM paper_sessions ORDER BY session")
    ]


def _status(
    db: Database,
    settings: Settings,
    clock: Clock,
    git_commit: str,
    data_end: date | None,
    state: Literal["NOT_STARTED", "RUNNING", "HALTED"],
    reason: str,
    benchmark: list[EquityPoint],
) -> PaperStatus:
    paper = _validate_mode(settings)
    books = db.query("SELECT start, simulation FROM paper_book")
    simulation = (
        SimulationSettings.model_validate_json(books[0]["simulation"])
        if books
        else paper.simulation()
    )
    start = date.fromisoformat(books[0]["start"]) if books else paper.start
    rows = db.query("SELECT session FROM paper_sessions ORDER BY session")
    try:
        recorded = _recorded(db)
    except ValueError:
        recorded = []  # A corrupted payload must not prevent publishing HALTED.
    latest = recorded[-1] if recorded else None
    capital = simulation.initial_capital_usd
    equity = latest.equity_usd if latest else (None if rows else capital)
    points = [
        EquityPoint(
            s.session, s.equity_usd, sum((p.market_value_usd for p in s.positions), Decimal(0))
        )
        for s in recorded
    ]
    calendar = TradingCalendar()
    now = clock.now()
    completed = now.date()
    while not calendar.is_session(completed) or calendar.session_close(completed) > now:
        completed = calendar.previous_session(completed)
    return PaperStatus(
        state=state,
        reason=reason,
        as_of=now,
        start=start,
        last_session=date.fromisoformat(rows[-1]["session"]) if rows else None,
        sessions_recorded=len(rows),
        data_end=data_end,
        data_stale=data_end is None or data_end < completed,
        equity_usd=equity,
        equity_jpy=equity * simulation.fx_jpy_per_usd if equity is not None else None,
        return_since_start=equity / capital - 1 if equity is not None else None,
        max_drawdown_since_start=max_drawdown(capital, points)[0] if equity is not None else None,
        positions=latest.positions if latest else (),
        next_intents=latest.next_intents if latest else (),
        b0_return_since_start=benchmark[-1].equity_usd / capital - 1 if benchmark else None,
        git_commit=git_commit,
    )


def _validate_mode(settings: Settings) -> PaperSettings:
    if settings.mode is not Mode.PAPER or settings.broker_environment is not BrokerEnvironment.SIM:
        raise ConfigError("paper run requires PAPER mode and SIM broker environment")
    if settings.paper is None:
        raise ConfigError("PAPER requires a paper section")
    return settings.paper


def run_paper(
    settings: Settings,
    source: DailyBarSource,
    adjusted: DailyBarSource | None,
    state_dir: Path,
    through: date | None,
    clock: Clock,
    git_commit: str,
) -> PaperRun:
    """Replay from inception under one lock; a mismatch commits no book or session changes."""
    paper = _validate_mode(settings)
    state_dir.mkdir(parents=True, exist_ok=True)
    with SingleInstanceLock(state_dir / "paper.db"), Database(state_dir / "paper.db") as db:
        state: Literal["NOT_STARTED", "RUNNING", "HALTED"] = "RUNNING"
        reason = "Replay verified"
        code = added = 0
        data_end = None
        benchmark: list[EquityPoint] = []
        try:
            strategy = MomentumStrategy()
            book = _book(settings, paper, strategy)
            spy = source.bars("SPY")
            if not spy:
                raise DataError("no SPY sessions in local data")
            data_end = spy[-1].ts.date()
            _verify_book(db, book)
            last = through if through is not None else data_end
            if last > data_end:
                raise ValueError(f"through {last} is later than data_end {data_end}")
            first = date.fromisoformat(book["start"])
            if last < first or not TradingCalendar().sessions(first, last):
                state, reason = "NOT_STARTED", "The book has not started at through"
                _verify_sessions(db, [], last)
            else:
                result = run_backtest(
                    settings,
                    strategy,
                    source,
                    adjusted=adjusted,
                    start=first,
                    end=last,
                    simulation=paper.simulation(),
                    capture_sessions=True,
                )
                _verify_sessions(db, result.sessions, last)
                rows = db.query("SELECT MAX(session) AS last FROM paper_sessions")
                latest = rows[0]["last"]
                new = [
                    s for s in result.sessions if latest is None or s.session.isoformat() > latest
                ]
                with db.transaction() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_book "
                        "(id, start, strategy_id, strategy_version, "
                        "params_hash, simulation, risk_hash) "
                        "VALUES (1, ?, ?, ?, ?, ?, ?)",
                        tuple(book.values()),
                    )
                    conn.executemany(
                        "INSERT INTO paper_sessions "
                        "(session, snapshot_json, snapshot_hash, "
                        "recorded_at, git_commit, data_snapshot_hash) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        [
                            (
                                s.session.isoformat(),
                                s.snapshot_json,
                                s.snapshot_hash,
                                clock.now().isoformat(),
                                git_commit,
                                source.snapshot_hash,
                            )
                            for s in new
                        ],
                    )
                added = len(new)
                previous = paper.initial_capital_usd
                new_dates = {s.session for s in new}
                for snapshot in result.sessions:
                    if snapshot.session in new_dates:
                        write_daily(state_dir, snapshot, previous)
                    previous = snapshot.equity_usd
                recorded_last = max(last, date.fromisoformat(latest)) if latest else last
                benchmark = buy_and_hold(
                    [bar for bar in spy if first <= bar.ts.date() <= recorded_last],
                    paper.initial_capital_usd,
                    WEBULL_JP,
                    ExecutionAssumptions(),
                    distributions={
                        a.session: a.cash_per_share
                        for a in result.corporate_actions.actions("SPY")
                        if a.kind is CorporateActionKind.DISTRIBUTION
                    }
                    if result.corporate_actions
                    else {},
                    dividend_us_withholding_pct=paper.dividend_us_withholding_pct,
                    dividend_jp_withholding_pct=paper.dividend_jp_withholding_pct,
                )
        except PaperMismatchError as exc:
            state, reason, code = "HALTED", str(exc), EXIT_PAPER_MISMATCH
        except DataError as exc:
            state, reason, code = "HALTED", str(exc), EXIT_DATA_ERROR
        except ValueError as exc:
            state, reason, code = "HALTED", str(exc), EXIT_USAGE_ERROR
        status = _status(db, settings, clock, git_commit, data_end, state, reason, benchmark)
        write_status(state_dir, status)
        return PaperRun(status, added, code)


def halt_data_failure(
    settings: Settings, state_dir: Path, clock: Clock, git_commit: str, reason: str
) -> PaperRun:
    """Publish a data-loading failure even when no source could be constructed."""
    _validate_mode(settings)
    state_dir.mkdir(parents=True, exist_ok=True)
    with SingleInstanceLock(state_dir / "paper.db"), Database(state_dir / "paper.db") as db:
        status = _status(db, settings, clock, git_commit, None, "HALTED", reason, [])
        write_status(state_dir, status)
        return PaperRun(status, 0, EXIT_DATA_ERROR)
