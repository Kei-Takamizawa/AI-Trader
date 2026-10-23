import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aitrader.cli.main import EXIT_CONFIG_ERROR, EXIT_LIVE_DISABLED, app

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"
runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("aitrader ")


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output


@pytest.mark.parametrize("mode", ["backtest", "paper"])
def test_config_validate_prints_mode_and_hash(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBULL_APP_KEY", raising=False)
    result = runner.invoke(
        app, ["config", "validate", "--mode", mode, "--config-dir", str(REPO_CONFIG)]
    )
    assert result.exit_code == 0, result.output
    assert f"mode: {mode}" in result.output
    assert re.search(r"config_hash: [0-9a-f]{64}", result.output)


def test_config_validate_reports_errors_with_exit_code_2(tmp_path: Path) -> None:
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    (target / "paper.yaml").write_text(
        "mode: paper\nbroker_environment: sim\npaper: {}\nrisk:\n  max_drawdown_pct: 50\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["config", "validate", "--mode", "paper", "--config-dir", str(target)]
    )
    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "exceeds the hard cap" in result.output


def test_config_validate_live_is_disabled() -> None:
    result = runner.invoke(
        app, ["config", "validate", "--mode", "live", "--config-dir", str(REPO_CONFIG)]
    )
    assert result.exit_code == EXIT_LIVE_DISABLED
    assert "LIVE is disabled in this build" in result.output


def test_live_command_is_disabled() -> None:
    result = runner.invoke(app, ["live"])
    assert result.exit_code == EXIT_LIVE_DISABLED
    assert "LIVE is disabled in this build" in result.output
