"""Universe U0 (document 11 §2)."""

from pathlib import Path

import pytest

from aitrader.config import ConfigError, load_universe

REPO = Path(__file__).resolve().parents[3]


def test_u0_is_fixed_and_contains_only_etfs_from_the_design() -> None:
    universe = load_universe(REPO / "config" / "universe.yaml")
    assert universe.universe == "U0"
    assert len(universe.symbols) == 23
    assert {"SPY", "QQQ", "IWM", "BIL", "TLT", "GLD"} <= set(universe.symbols)
    sectors = {"XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"}
    assert sectors <= set(universe.symbols)


@pytest.mark.parametrize(
    "text",
    [
        "universe: X\nversion: 1\nsymbols: [SPY, SPY]\n",
        "universe: X\nversion: 1\nsymbols: [spy]\n",
        "universe: X\nversion: 1\nsymbols: []\n",
        "universe: X\nversion: 0\nsymbols: [SPY]\n",
        "symbols: [SPY\n",
    ],
)
def test_invalid_universes_are_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "u.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_universe(path)


def test_missing_universe_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_universe(tmp_path / "none.yaml")
