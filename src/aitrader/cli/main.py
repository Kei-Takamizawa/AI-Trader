"""``aitrader`` command-line interface."""

import os
from datetime import date
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer

from aitrader.backtest import build_manifest, metrics_for, run_backtest, write_results
from aitrader.clock import TradingCalendar
from aitrader.config import (
    ConfigError,
    LiveDisabledError,
    Secrets,
    assert_no_live_credentials,
    config_hash,
    load_settings,
    load_universe,
)
from aitrader.data import (
    AlpacaDataClient,
    DailyBarSource,
    DataError,
    DataSourceError,
    fetch_universe,
)
from aitrader.domain import Mode
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
    data_dir: Annotated[Path, typer.Option(help="Directory with <SYMBOL>.csv daily bars.")],
    out_dir: Annotated[Path, typer.Option(help="Where to write manifest, metrics and equity.")],
    strategy: Annotated[str, typer.Option(help="Strategy ID.")] = "S2",
    config_dir: Annotated[Path, typer.Option(help="Configuration directory.")] = Path("config"),
    start: Annotated[str | None, typer.Option(help="First session (YYYY-MM-DD).")] = None,
    end: Annotated[str | None, typer.Option(help="Last session (YYYY-MM-DD).")] = None,
    git_commit: Annotated[str, typer.Option(help="Commit recorded in the manifest.")] = "unknown",
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
        source = DailyBarSource(data_dir, calendar)
    except DataError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_DATA_ERROR) from exc
    chosen = STRATEGIES[strategy]()
    result = run_backtest(settings, chosen, source, start=first, end=last, calendar=calendar)
    manifest = build_manifest(
        settings=settings,
        strategy=chosen,
        source=source,
        start=first,
        end=last,
        git_commit=git_commit,
        variants_tried=variants_tried,
    )
    write_results(out_dir, manifest, metrics_for(result, source, settings), result)
    typer.echo(f"run_id: {manifest['run_id']}")
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
    secrets = Secrets()
    if secrets.data_api_key is None or secrets.data_api_secret is None:
        typer.echo(
            "error: set AITRADER_DATA_API_KEY and AITRADER_DATA_API_SECRET "
            "(environment secrets; never in files)",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG_ERROR)
    calendar = TradingCalendar()
    try:
        client = AlpacaDataClient(secrets.data_api_key, secrets.data_api_secret)
        report = fetch_universe(
            client, symbols, date.fromisoformat(start), date.fromisoformat(end), out_dir, calendar
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
