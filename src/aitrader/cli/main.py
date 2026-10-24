"""``aitrader`` command-line interface."""

import os
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer

from aitrader.app.paper import EXIT_PAPER_MISMATCH as EXIT_PAPER_MISMATCH
from aitrader.app.paper import halt_data_failure, run_paper
from aitrader.backtest import build_manifest, metrics_for, run_backtest, write_results
from aitrader.backtest.walkforward import (
    run_walkforward,
    validate_research_options,
    write_walkforward,
)
from aitrader.clock import CalendarError, TradingCalendar, WallClock
from aitrader.config import (
    ConfigError,
    LiveDisabledError,
    Secrets,
    assert_no_live_credentials,
    config_hash,
    load_settings,
    load_universe,
)
from aitrader.config.loader import load_webull_sandbox_settings
from aitrader.data import (
    AlpacaDataClient,
    DailyBarSource,
    DataError,
    DataSourceError,
    fetch_universe,
)
from aitrader.domain import Mode
from aitrader.storage import InstanceLockedError
from aitrader.strategy import STRATEGIES

EXIT_CONFIG_ERROR = 2
EXIT_LIVE_DISABLED = 3
EXIT_DATA_ERROR = 4
EXIT_USAGE_ERROR = 5

app = typer.Typer(
    name="aitrader",
    help="Risk-first automated trading system (BACKTEST / PAPER / LIVE).",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Configuration commands.", no_args_is_help=True)
app.add_typer(config_app, name="config")
data_app = typer.Typer(help="Market data commands (read-only).", no_args_is_help=True)
app.add_typer(data_app, name="data")
sandbox_app = typer.Typer(help="Operator-only Webull UAT checks.")
app.add_typer(sandbox_app, name="sandbox")
paper_app = typer.Typer(help="Local daily PAPER replay.", no_args_is_help=True)
app.add_typer(paper_app, name="paper")


def _version() -> str:
    try:
        return package_version("aitrader")
    except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
        return "0+unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aitrader {_version()}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    """Risk-first automated trading system."""


@config_app.command("validate")
def validate(
    mode: Annotated[Mode, typer.Option(help="Mode whose configuration to validate.")],
    config_dir: Annotated[
        Path, typer.Option(help="Directory with base.yaml and <mode>.yaml.")
    ] = Path("config"),
) -> None:
    """Validate the configuration for MODE and print its hash."""
    try:
        settings = load_settings(mode, config_dir)
    except LiveDisabledError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(EXIT_LIVE_DISABLED) from exc
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    typer.echo(f"mode: {settings.mode.value}")
    typer.echo(f"broker_environment: {settings.broker_environment.value}")
    typer.echo(f"config_hash: {config_hash(settings)}")


@app.command()
def backtest(
    data_dir: Annotated[
        Path,
        typer.Option(help="Directory with raw/ and adjusted/, or flat <SYMBOL>.csv daily bars."),
    ],
    out_dir: Annotated[Path, typer.Option(help="Where to write manifest, metrics and equity.")],
    strategy: Annotated[str, typer.Option(help="Strategy ID.")] = "S2",
    config_dir: Annotated[Path, typer.Option(help="Configuration directory.")] = Path("config"),
    start: Annotated[str | None, typer.Option(help="First session (YYYY-MM-DD).")] = None,
    end: Annotated[str | None, typer.Option(help="Last session (YYYY-MM-DD).")] = None,
    git_commit: Annotated[str | None, typer.Option(help="Commit recorded in the manifest.")] = None,
    variants_tried: Annotated[
        int, typer.Option(help="Strategy variants tried so far (document 12 §2).")
    ] = 1,
) -> None:
    """Run a backtest on CSV bars and write a reproducible run manifest and metrics."""
    try:
        settings = load_settings(Mode.BACKTEST, config_dir)
        period = settings.require_backtest()
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    if strategy not in STRATEGIES:
        typer.echo(
            f"error: unknown strategy {strategy!r}; known: {', '.join(STRATEGIES)}", err=True
        )
        raise typer.Exit(EXIT_USAGE_ERROR)
    first = date.fromisoformat(start) if start else period.start
    last = date.fromisoformat(end) if end else period.end
    calendar = TradingCalendar()
    try:
        source, adjusted = _bar_sources(data_dir, calendar)
        chosen = STRATEGIES[strategy]()
        result = run_backtest(
            settings, chosen, source, adjusted=adjusted, start=first, end=last, calendar=calendar
        )
    except DataError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_DATA_ERROR) from exc
    manifest = build_manifest(
        settings=settings,
        strategy=chosen,
        source=source,
        adjusted=adjusted,
        start=first,
        end=last,
        git_commit=_git_commit(git_commit, required=False),
        variants_tried=variants_tried,
    )
    write_results(out_dir, manifest, metrics_for(result, source, settings), result)
    typer.echo(f"run_id: {manifest['run_id']}")
    typer.echo(f"results: {out_dir}")


def _bar_sources(
    data_dir: Path, calendar: TradingCalendar
) -> tuple[DailyBarSource, DailyBarSource | None]:
    has_raw = (data_dir / "raw").is_dir()
    has_adjusted = (data_dir / "adjusted").is_dir()
    if has_raw != has_adjusted:
        missing = "adjusted/" if has_raw else "raw/"
        raise DataError(f"missing {missing} in {data_dir}; both series are required")
    source = DailyBarSource(data_dir / "raw" if has_raw else data_dir, calendar)
    adjusted = DailyBarSource(data_dir / "adjusted", calendar) if has_adjusted else None
    return source, adjusted


def _git_commit(explicit: str | None = None, *, required: bool = True) -> str:
    """Prefer operator provenance and resolve Git through the current PATH."""
    if explicit is not None:
        return explicit
    try:
        executable = shutil.which("git")
        if executable is None:
            raise FileNotFoundError("git is not on PATH")
        # The executable is resolved by PATH as required; arguments are fixed, with no shell.
        return subprocess.check_output(  # noqa: S603
            [executable, "rev-parse", "HEAD"], text=True, stderr=subprocess.PIPE
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        if required:
            raise DataError("cannot read the git commit; --git-commit can be passed") from exc
        return "unknown"


@app.command()
def walkforward(
    data_dir: Annotated[Path, typer.Option(help="Raw/adjusted directories or flat CSV bars.")],
    out_dir: Annotated[Path, typer.Option(help="Walk-forward output directory.")],
    strategy: Annotated[str, typer.Option(help="Strategy ID: S1 or S2.")] = "S1",
    config_dir: Annotated[Path, typer.Option(help="Configuration directory.")] = Path("config"),
    start: Annotated[str, typer.Option(help="First training session.")] = "2017-01-03",
    train_months: Annotated[int, typer.Option()] = 36,
    test_months: Annotated[int, typer.Option()] = 6,
    holdout_months: Annotated[int, typer.Option()] = 24,
    git_commit: Annotated[str | None, typer.Option(help="Commit recorded in the manifest.")] = None,
    risk_per_trade_pct: Annotated[
        str | None, typer.Option(help="Research risk percent (BACKTEST only). ")
    ] = None,
    cash_proxy: Annotated[str | None, typer.Option(help="S1 empty-slot cash proxy symbol.")] = None,
    prior_variants: Annotated[int, typer.Option(help="Variants tried before this grid.")] = 0,
) -> None:
    """Select on training windows and evaluate subsequent tests, leaving hold-out locked."""
    if strategy not in ("S1", "S2"):
        typer.echo(f"error: unknown strategy {strategy!r}; known: S1, S2", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR)
    try:
        settings = load_settings(Mode.BACKTEST, config_dir)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    try:
        risk_override = Decimal(risk_per_trade_pct) if risk_per_trade_pct is not None else None
        validate_research_options(settings, strategy, risk_override, cash_proxy, prior_variants)
    except (InvalidOperation, ValueError) as exc:
        typer.echo(f"error: invalid research options: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc
    try:
        calendar = TradingCalendar()
        source, adjusted = _bar_sources(data_dir, calendar)
        manifest, result = run_walkforward(
            settings,
            strategy,
            source,
            adjusted=adjusted,
            start=date.fromisoformat(start),
            train_months=train_months,
            test_months=test_months,
            holdout_months=holdout_months,
            git_commit=_git_commit(git_commit),
            calendar=calendar,
            risk_per_trade_pct=risk_override,
            cash_proxy=cash_proxy,
            prior_variants=prior_variants,
        )
    except DataError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_DATA_ERROR) from exc
    write_walkforward(out_dir, manifest, result)
    typer.echo(f"results: {out_dir}")


@data_app.command("fetch")
def data_fetch(
    end: Annotated[str, typer.Option(help="Last session (YYYY-MM-DD); fixed for reproducibility.")],
    out_dir: Annotated[Path, typer.Option(help="Where raw/ and adjusted/ are written.")] = Path(
        "data/bars"
    ),
    universe: Annotated[Path, typer.Option(help="Universe file.")] = Path("config/universe.yaml"),
    start: Annotated[str, typer.Option(help="First session (YYYY-MM-DD).")] = "2016-01-04",
) -> None:
    """Download daily bars for the universe from Alpaca (read-only) and check them."""
    try:
        assert_no_live_credentials(os.environ, Mode.PAPER)
        symbols = load_universe(universe).symbols
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    calendar = TradingCalendar()
    now = WallClock().now()
    cutoff = now - timedelta(minutes=16)
    last_closed = now.date()
    while not calendar.is_session(last_closed) or calendar.session_close(last_closed) > cutoff:
        last_closed = calendar.previous_session(last_closed)
    try:
        last = date.fromisoformat(end)
        if not calendar.is_session(last) or calendar.session_close(last) > cutoff:
            raise ValueError("end must be a session closed at least 16 minutes ago")
    except (ValueError, CalendarError) as exc:
        typer.echo(f"error: {exc}; last closed session: {last_closed}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    secrets = Secrets()
    if secrets.data_api_key is None or secrets.data_api_secret is None:
        typer.echo(
            "error: set AITRADER_DATA_API_KEY and AITRADER_DATA_API_SECRET "
            "(environment secrets; never in files)",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)
    try:
        client = AlpacaDataClient(secrets.data_api_key, secrets.data_api_secret)
        report = fetch_universe(
            client, symbols, date.fromisoformat(start), last, out_dir, calendar, now
        )
    except DataSourceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_DATA_ERROR) from exc
    for symbol, info in report.symbols.items():
        typer.echo(
            f"{symbol}: {info.rows_raw} sessions {info.first} .. {info.last}; "
            f"missing {len(info.missing_sessions)}, large moves {len(info.large_moves)}, "
            f"corporate actions {len(info.corporate_actions)}"
        )
    typer.echo(f"snapshot hashes: {report.snapshot_hashes}")


@app.command()
def live() -> None:
    """Start LIVE trading. Disabled in this build."""
    typer.echo("LIVE is disabled in this build", err=True)
    raise typer.Exit(EXIT_LIVE_DISABLED)


@sandbox_app.command("check")
def sandbox_check(
    config_dir: Annotated[Path, typer.Option(help="Configuration directory.")] = Path("config"),
    out_dir: Annotated[Path, typer.Option(help="Sanitised sandbox reports.")] = Path(
        "reports/sandbox"
    ),
) -> None:
    """Probe shared sandbox accounts. Only the operator runs this against Webull."""
    from aitrader.broker.webull import SandboxCredentials, WebullBroker
    from aitrader.broker.webull.check import SandboxCheck, write_auth_failure
    from aitrader.broker.webull.sdk import TokenInitializationError

    try:
        credentials = SandboxCredentials()
        settings = load_webull_sandbox_settings(config_dir)
        clock = WallClock()
        broker = WebullBroker(
            clock,
            settlement_date=TradingCalendar().settlement_date,
            credentials=credentials,
            settings=settings,
        )
    except TokenInitializationError as exc:
        path, exit_code = write_auth_failure(out_dir, clock, settings.endpoint.lower(), exc)
        typer.echo(f"sandbox report: {path}")
        raise typer.Exit(exit_code) from None
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    check = SandboxCheck(broker, clock)
    broker.set_observer(check.observe)
    try:
        path, exit_code = check.run(out_dir)
    finally:
        broker.close()
    typer.echo(f"sandbox report: {path}")
    raise typer.Exit(exit_code)


@paper_app.command("run")
def paper_run(
    config: Annotated[
        Path, typer.Option(help="PAPER YAML; base.yaml is read alongside it.")
    ] = Path("config/paper.yaml"),
    data_dir: Annotated[Path, typer.Option(help="Local daily bars.")] = Path("data/bars"),
    state_dir: Annotated[Path, typer.Option(help="Persistent PAPER book and reports.")] = Path(
        "data/paper"
    ),
    through: Annotated[str | None, typer.Option(help="Last session (YYYY-MM-DD).")] = None,
    git_commit: Annotated[str | None, typer.Option(help="Source commit provenance.")] = None,
) -> None:
    """Verify recorded sessions and catch up a dedicated virtual portfolio."""
    try:
        settings = load_settings(Mode.PAPER, config.parent, config_file=config)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR) from exc
    try:
        last = date.fromisoformat(through) if through is not None else None
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc
    clock = WallClock()
    try:
        commit = _git_commit(git_commit)
        try:
            source, adjusted = _bar_sources(data_dir, TradingCalendar())
        except DataError as exc:
            result = halt_data_failure(settings, state_dir, clock, commit, str(exc))
        else:
            result = run_paper(settings, source, adjusted, state_dir, last, clock, commit)
    except InstanceLockedError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE_ERROR) from exc
    except DataError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_DATA_ERROR) from exc
    status = result.status
    typer.echo(
        f"{status.state} sessions_added={result.sessions_added} "
        f"last_session={status.last_session} equity_usd={status.equity_usd}"
        + (f" reason={status.reason}" if result.exit_code else "")
    )
    raise typer.Exit(result.exit_code)
