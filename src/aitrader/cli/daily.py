"""Sequential PAPER job orchestration; storage remains outside replay and risk code."""

import json
import os
import sqlite3
from contextlib import suppress
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from aitrader.app.paper import PaperRun, halt_data_failure, run_paper
from aitrader.clock import Clock, TradingCalendar, WallClock
from aitrader.cloud import (
    CloudError,
    GcsStateStore,
    NotFound,
    PreconditionFailed,
    StateStore,
    pack,
    parse_gs_uri,
    unpack,
)
from aitrader.config import (
    ConfigError,
    Secrets,
    assert_no_live_credentials,
    load_settings,
    load_universe,
)
from aitrader.data import AlpacaDataClient, DataError, DataSourceError, fetch_universe
from aitrader.domain import Mode
from aitrader.storage import InstanceLockedError


def _log(clock: Clock, event: str, code: int = 0, /, **values: object) -> None:
    entry = dict(
        ts=clock.now().isoformat(),
        severity="ERROR" if code else "INFO",
        event=event,
        exit_code=code,
        **values,
    )
    if code == 6:
        entry["action"] = "stop; do not delete state; report to the designer"
    print(json.dumps(entry, default=str))


def fetch(config_dir: Path, work_dir: Path, clock: Clock) -> dict[str, object]:
    """Use exactly the data-fetch inputs without its human-readable CLI output."""
    from aitrader.cli.main import last_closed_session

    calendar = TradingCalendar()
    now = clock.now()
    last = last_closed_session(calendar, now)
    symbols = load_universe(config_dir / "universe.yaml").symbols
    secrets = Secrets()
    if secrets.data_api_key is None or secrets.data_api_secret is None:
        raise ConfigError("read-only data credentials are required")
    report = fetch_universe(
        AlpacaDataClient(secrets.data_api_key, secrets.data_api_secret),
        symbols,
        date(2016, 1, 4),
        last,
        work_dir / "data/bars",
        calendar,
        now,
    )
    return {"session": last, "symbols": symbols, "snapshot_hashes": report.snapshot_hashes}


def book(config_dir: Path, work_dir: Path, name: str, clock: Clock, commit: str) -> PaperRun:
    from aitrader.cli.main import _bar_sources

    settings = load_settings(Mode.PAPER, config_dir, config_file=config_dir / f"{name}.yaml")
    state = work_dir / "data" / name
    try:
        source, adjusted = _bar_sources(work_dir / "data/bars", TradingCalendar())
    except DataError as exc:
        return halt_data_failure(settings, state, clock, commit, str(exc))
    return run_paper(settings, source, adjusted, state, None, clock, commit)


def _restore(store: StateStore, name: str, work: Path, init: bool) -> int:
    try:
        data, generation = store.download(name)
    except NotFound:
        if not init:
            raise NotFound("state archive missing; pass --init to create it") from None
        if any((work / root).exists() for root in ("data/bars", "data/paper", "data/paper-s3")):
            raise PreconditionFailed("--init requires empty work directories") from None
        return 0
    if init:
        raise PreconditionFailed("--init refused: state archive already exists")
    # A cloud restore must not overlay stale files from an unrelated local book.
    if any((work / root).exists() for root in ("data/bars", "data/paper", "data/paper-s3")):
        raise PreconditionFailed("restore requires empty work directories")
    unpack(data, work)
    return generation


def _upload_reports(store: StateStore, prefix: str, work: Path, before: set[Path]) -> None:
    for name in ("paper", "paper-s3"):
        root = work / "data" / name
        status = root / "status/latest.json"
        if status.exists():
            key = f"{prefix.rstrip('/')}/{name}/status/latest.json"
            store.upload(key, status.read_bytes(), _latest_generation(store, key))
    for name in ("paper", "paper-s3"):
        root = work / "data" / name
        for path in sorted((root / "reports/daily").glob("*")):
            if path not in before and path.is_file():
                with suppress(PreconditionFailed):
                    store.upload(
                        f"{prefix.rstrip('/')}/{name}/{path.relative_to(root).as_posix()}",
                        path.read_bytes(),
                        0,
                    )


def _latest_generation(store: StateStore, name: str) -> int:
    try:
        return store.download(name)[1]
    except NotFound:
        return 0


def _execute(
    state_uri: str | None,
    reports_uri: str | None,
    work: Path,
    config_dir: Path,
    commit: str,
    init: bool,
    clock: Clock,
) -> int:
    stage = "daily.restore"
    try:
        store: StateStore | None = None
        reports_store: StateStore | None = None
        name, prefix, generation = "", "", 0
        if state_uri is not None:
            bucket, name = parse_gs_uri(state_uri)
            report_bucket, prefix = parse_gs_uri(reports_uri or f"gs://{bucket}/reports/")
            store = GcsStateStore(bucket)
            reports_store = store if report_bucket == bucket else GcsStateStore(report_bucket)
            generation = _restore(store, name, work, init)
        elif init or reports_uri is not None:
            raise ValueError("--init and --reports-uri require --state-uri")
        _log(clock, stage, generation=generation)
        stage = "daily.fetch"
        summary = fetch(config_dir, work, clock)
        _log(clock, stage, **summary)
        before = set(work.glob("data/paper*/reports/daily/*"))
        codes = []
        for label in ("paper", "paper-s3"):
            stage = "daily.book"
            try:
                result = book(config_dir, work, label, clock, commit)
            except (ConfigError, DataError, InstanceLockedError, OSError, sqlite3.Error) as exc:
                code = (
                    2
                    if isinstance(exc, ConfigError)
                    else (5 if isinstance(exc, InstanceLockedError) else 4)
                )
                _log(clock, stage, code, book=label, message="book failed")
                codes.append(code)
            else:
                codes.append(result.exit_code)
                _log(
                    clock,
                    stage,
                    result.exit_code,
                    book=label,
                    state=result.status.state,
                    sessions_added=result.sessions_added,
                    equity=result.status.equity_usd,
                    session=result.status.last_session,
                )
        stage = "daily.upload"
        if store is not None and reports_store is not None:
            # Keep the previous archive replayable until all new reports are published.
            try:
                _upload_reports(reports_store, prefix, work, before)
            except PreconditionFailed:
                raise CloudError("report upload failed") from None
            try:
                generation = store.upload(name, pack(work), generation)
            except PreconditionFailed:
                raise PreconditionFailed(
                    "another writer changed the state; nothing retried"
                ) from None
        _log(clock, stage, generation=generation)
        return max(codes)
    except (CloudError, DataError, DataSourceError, ConfigError, ValueError, OSError) as exc:
        code = (
            5
            if isinstance(exc, (PreconditionFailed, ValueError))
            else (2 if isinstance(exc, ConfigError) else 4)
        )
        message = (
            str(exc) if isinstance(exc, (NotFound, PreconditionFailed)) else "daily step failed"
        )
        _log(clock, stage, code, message=message)
        return code


def run_daily(
    state_uri: str | None,
    reports_uri: str | None,
    work_dir: Path | None,
    config_dir: Path,
    git_commit: str | None,
    init: bool,
) -> int:
    from aitrader.cli.main import _git_commit

    clock = WallClock()
    # This guard precedes all storage, credential loading and workspace operations.
    try:
        assert_no_live_credentials(os.environ, Mode.PAPER)
    except ConfigError:
        _log(clock, "daily.start", 2, message="PAPER credential guard failed")
        _log(clock, "daily.end", 2)
        return 2
    _log(clock, "daily.start")
    try:
        commit = _git_commit(git_commit or os.environ.get("AITRADER_GIT_COMMIT"))
        if state_uri is not None and work_dir is None:
            with TemporaryDirectory(prefix="aitrader-daily-") as temporary:
                code = _execute(
                    state_uri, reports_uri, Path(temporary), config_dir, commit, init, clock
                )
        else:
            code = _execute(
                state_uri, reports_uri, work_dir or Path("."), config_dir, commit, init, clock
            )
    except DataError, OSError:
        code = 4
    _log(clock, "daily.end", code)
    return code
