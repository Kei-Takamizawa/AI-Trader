"""Reading the system clock outside aitrader.clock is a lint error (TASK-001, AC9)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

PROBES = {
    "datetime.now": "from datetime import UTC, datetime\n\nx = datetime.now(UTC)\n",
    "date.today": "from datetime import date\n\nx = date.today()\n",
    "time.time": "import time\n\nx = time.time()\n",
}


def _ruff(source: str, filename: str) -> subprocess.CompletedProcess[str]:
    candidate = Path(sys.executable).parent / "ruff"
    ruff = str(candidate) if candidate.exists() else shutil.which("ruff")
    if ruff is None:  # pragma: no cover
        pytest.skip("ruff is not installed")
    return subprocess.run(
        [ruff, "check", "--select", "TID251", "--stdin-filename", filename, "-"],
        input=source,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize("name", sorted(PROBES))
def test_clock_reads_are_banned_outside_the_clock_package(name: str) -> None:
    result = _ruff(PROBES[name], "src/aitrader/risk/_probe.py")
    assert result.returncode != 0
    assert "TID251" in result.stdout


@pytest.mark.parametrize("name", sorted(PROBES))
def test_clock_package_may_read_the_system_clock(name: str) -> None:
    result = _ruff(PROBES[name], "src/aitrader/clock/_probe.py")
    assert result.returncode == 0, result.stdout
