"""The dependency rules must actually catch violations (TASK-001, AC8)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _tool(name: str) -> str:
    candidate = Path(sys.executable).parent / name
    found = str(candidate) if candidate.exists() else shutil.which(name)
    if found is None:  # pragma: no cover - dev dependencies are always installed in CI
        pytest.skip(f"{name} is not installed")
    return found


def _copy_project(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "src", tmp_path / "src")
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


def _lint_imports(project: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(project / "src"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    return subprocess.run(
        [_tool("lint-imports"), "--no-cache"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_unmodified_project_keeps_all_contracts(tmp_path: Path) -> None:
    result = _lint_imports(_copy_project(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("module", "statement"),
    [
        ("risk/__init__.py", "import aitrader.ai"),
        ("app/paper.py", "import aitrader.broker.webull"),
        ("execution/__init__.py", "from aitrader import ai"),
        ("strategy/__init__.py", "import aitrader.broker"),
        ("domain/money.py", "import aitrader.config"),
        ("fees/calc.py", "import aitrader.risk"),
        ("strategy/__init__.py", "import aitrader.fees"),
        ("portfolio/ledger.py", "import aitrader.config"),
        ("execution/manager.py", "import aitrader.broker.sim"),
        ("indicators/__init__.py", "import aitrader.strategy"),
        ("data/bars.py", "import pandas"),
        ("broker/sim/__init__.py", "import webull"),
        ("broker/port.py", "import webull"),
        ("execution/manager.py", "import aitrader.broker.webull"),
        ("risk/engine.py", "import exchange_calendars"),
    ],
)
def test_forbidden_import_breaks_a_contract(tmp_path: Path, module: str, statement: str) -> None:
    project = _copy_project(tmp_path)
    target = project / "src" / "aitrader" / module
    target.write_text(
        target.read_text(encoding="utf-8") + f"\n{statement}  # probe\n", encoding="utf-8"
    )
    result = _lint_imports(project)
    assert result.returncode != 0
    assert "BROKEN" in result.stdout
