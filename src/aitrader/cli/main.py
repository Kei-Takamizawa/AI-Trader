"""``aitrader`` command-line interface."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer

from aitrader.config import ConfigError, LiveDisabledError, config_hash, load_settings
from aitrader.domain import Mode

EXIT_CONFIG_ERROR = 2
EXIT_LIVE_DISABLED = 3

app = typer.Typer(
    name="aitrader",
    help="Risk-first automated trading system (BACKTEST / PAPER / LIVE).",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Configuration commands.", no_args_is_help=True)
app.add_typer(config_app, name="config")


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
def live() -> None:
    """Start LIVE trading. Disabled in this build."""
    typer.echo("LIVE is disabled in this build", err=True)
    raise typer.Exit(EXIT_LIVE_DISABLED)
