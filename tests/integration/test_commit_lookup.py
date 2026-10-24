"""Both CLI commands record explicit or PATH-resolved provenance without real Git calls."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from typer.testing import CliRunner

from aitrader.cli.main import app
from tests.integration.test_walkforward import synthetic


@pytest.mark.parametrize("command", ["backtest", "walkforward"])
@pytest.mark.parametrize("lookup", ["explicit", "success", "missing", "command_error", "os_error"])
def test_commit_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, lookup: str
) -> None:
    which = Mock(return_value=None if lookup == "missing" else "/custom/bin/git")
    output = Mock(return_value="resolved-head\n")
    if lookup == "command_error":
        output.side_effect = subprocess.CalledProcessError(1, "git")
    elif lookup == "os_error":
        output.side_effect = OSError("unavailable")
    monkeypatch.setattr("aitrader.cli.main.shutil.which", which)
    monkeypatch.setattr("aitrader.cli.main.subprocess.check_output", output)
    out = tmp_path / "out"
    if command == "walkforward":
        synthetic(tmp_path)
        args = [
            "--start",
            "2021-10-04",
            "--train-months",
            "2",
            "--test-months",
            "1",
            "--holdout-months",
            "2",
        ]
        data = tmp_path
    else:
        data = Path("tests/fixtures/bars")
        args = ["--start", "2023-01-03", "--end", "2024-12-31"]
    if lookup == "explicit":
        args += ["--git-commit", "operator-head"]
    result = CliRunner().invoke(
        app, [command, "--data-dir", str(data), "--out-dir", str(out), *args]
    )
    failed = lookup in ("missing", "command_error", "os_error")
    if command == "walkforward" and failed:
        assert result.exit_code == 4, result.output
        assert "--git-commit can be passed" in result.output
        assert not out.exists()
    else:
        assert result.exit_code == 0, result.output
        manifest = yaml.safe_load((out / "manifest.yaml").read_text())
        expected = "operator-head" if lookup == "explicit" else "resolved-head"
        assert manifest["git_commit"] == ("unknown" if failed else expected)
    if lookup == "explicit":
        which.assert_not_called()
        output.assert_not_called()
    else:
        which.assert_called_once_with("git")
        if lookup == "missing":
            output.assert_not_called()
        else:
            output.assert_called_once_with(
                ["/custom/bin/git", "rev-parse", "HEAD"], text=True, stderr=subprocess.PIPE
            )
