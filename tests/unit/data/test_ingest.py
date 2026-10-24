"""Ingestion: CSV store, checks (DQ-2, DQ-3, DQ-5), fetch report and the CLI command."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from aitrader.cli import main as cli_main
from aitrader.clock import TradingCalendar
from aitrader.data import (
    AlpacaDataClient,
    DailyBarRow,
    DailyBarSource,
    fetch_universe,
    write_daily_csv,
)
from aitrader.data.ingest import corporate_actions, large_moves, missing_sessions
from tests.unit.data.alpaca_fakes import FakeTransport, bar, page, status

CAL = TradingCalendar()
D = Decimal
REPO = Path(__file__).resolve().parents[3]


def row(day: date, close: str, volume: int = 1000) -> DailyBarRow:
    price = D(close)
    return DailyBarRow(
        session=day, open=price, high=price, low=price, close=price, volume=D(volume)
    )


def test_csv_store_is_readable_by_the_bar_source(tmp_path: Path) -> None:
    rows = [row(date(2024, 7, 2), "100.5"), row(date(2024, 7, 3), "101.123456")]
    path = write_daily_csv(tmp_path, "SPY", rows)
    assert path.name == "SPY.csv"
    assert not list(tmp_path.glob(".*.tmp"))  # written atomically
    bars = DailyBarSource(tmp_path, CAL).bars("SPY")
    assert [b.close for b in bars] == [D("100.5"), D("101.123456")]


def test_missing_sessions_against_the_calendar() -> None:
    rows = [row(date(2024, 7, 1), "1"), row(date(2024, 7, 3), "1")]
    assert missing_sessions(rows, date(2024, 7, 5), CAL) == [date(2024, 7, 2), date(2024, 7, 5)]
    assert missing_sessions([], date(2024, 7, 5), CAL) == []


def test_large_moves() -> None:
    rows = [
        row(date(2024, 7, 1), "100"),
        row(date(2024, 7, 2), "125"),
        row(date(2024, 7, 3), "160"),
    ]
    assert large_moves(rows) == [(date(2024, 7, 3), D(160) / D(125) - 1)]


def test_corporate_actions_from_raw_versus_adjusted() -> None:
    days = [date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3), date(2024, 7, 5)]
    raw = [row(days[0], "400"), row(days[1], "100"), row(days[2], "101"), row(days[3], "100")]
    # adjusted for a 4:1 split on 2 July and a 1% distribution on 5 July
    adjusted = [row(days[0], "99"), row(days[1], "99"), row(days[2], "99.99"), row(days[3], "100")]
    events = corporate_actions(raw, adjusted)
    assert [(d, kind) for d, kind, _ in events] == [(days[1], "split"), (days[3], "distribution")]
    assert corporate_actions(raw, adjusted[:1]) == []


def test_rounding_of_adjusted_prices_is_not_a_corporate_action() -> None:
    days = [date(2024, 7, 1), date(2024, 7, 2)]
    raw = [row(days[0], "545.34"), row(days[1], "549.01")]
    adjusted = [row(days[0], "539.81"), row(days[1], "543.45")]  # ratios differ by 0.0013%
    assert corporate_actions(raw, adjusted) == []


def responses() -> list[Any]:
    spy = [
        bar("2024-07-01T04:00:00Z", "545.63", "545.88", "542.52", "545.34", 40297800),
        bar("2024-07-02T04:00:00Z", "543.70", "549.01", "543.65", "549.01", 40434800),
    ]
    adjusted = [
        bar("2024-07-01T04:00:00Z", "540.10", "540.35", "537.02", "539.81", 40297800),
        bar("2024-07-02T04:00:00Z", "538.19", "543.45", "538.14", "543.45", 40434800),
    ]
    return [page({"SPY": spy}), page({"SPY": adjusted})]


def test_fetch_universe_writes_both_series_and_a_report(tmp_path: Path) -> None:
    client = AlpacaDataClient(SecretStr("k"), SecretStr("s"), transport=FakeTransport(responses()))
    report = fetch_universe(
        client, ["SPY", "XLC"], date(2024, 7, 1), date(2024, 7, 2), tmp_path, CAL
    )
    assert (tmp_path / "raw" / "SPY.csv").exists()
    assert (tmp_path / "adjusted" / "SPY.csv").exists()
    assert not (tmp_path / "raw" / "XLC.csv").exists()  # no data: no file
    spy = report.symbols["SPY"]
    assert (spy.rows_raw, spy.rows_adjusted, spy.first, spy.last) == (
        2,
        2,
        date(2024, 7, 1),
        date(2024, 7, 2),
    )
    assert spy.missing_sessions == ()
    assert report.symbols["XLC"].first is None
    stored = json.loads((tmp_path / "fetch_report.json").read_text())
    assert stored["snapshot_hashes"]["raw"] == DailyBarSource(tmp_path / "raw", CAL).snapshot_hash
    assert set(stored["snapshot_hashes"]) == {"raw", "adjusted"}
    assert stored["symbols"]["SPY"]["corporate_actions_dq5"] == []
    assert stored["period"] == {"start": "2024-07-01", "end": "2024-07-02"}


# ---------------------------------------------------------------- command line


RUNNER = CliRunner()


def run(*args: str, env: dict[str, str] | None = None) -> Any:
    return RUNNER.invoke(cli_main.app, ["data", "fetch", *args], env=env)


KEYS = {"AITRADER_DATA_API_KEY": "k", "AITRADER_DATA_API_SECRET": "s"}


def test_cli_fetch_needs_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AITRADER_DATA_API_KEY", raising=False)
    monkeypatch.delenv("AITRADER_DATA_API_SECRET", raising=False)
    result = run("--end", "2024-07-02", "--universe", str(REPO / "config" / "universe.yaml"))
    assert result.exit_code == 2
    assert "AITRADER_DATA_API_KEY" in result.output


def test_cli_fetch_refuses_live_credentials(tmp_path: Path) -> None:
    result = run("--end", "2024-07-02", env={**KEYS, "LIVE_WEBULL_APP_KEY": "x"})
    assert result.exit_code == 2


def test_cli_fetch_downloads_and_summarises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text("universe: T\nversion: 1\nsymbols: [SPY]\n")

    def fake_client(key: SecretStr, secret: SecretStr) -> AlpacaDataClient:
        assert (key.get_secret_value(), secret.get_secret_value()) == ("k", "s")
        return AlpacaDataClient(key, secret, transport=FakeTransport(responses()))

    monkeypatch.setattr(cli_main, "AlpacaDataClient", fake_client)
    out = tmp_path / "bars"
    result = run(
        "--end",
        "2024-07-02",
        "--start",
        "2024-07-01",
        "--universe",
        str(universe),
        "--out-dir",
        str(out),
        env=KEYS,
    )
    assert result.exit_code == 0, result.output
    assert "SPY: 2 sessions 2024-07-01 .. 2024-07-02" in result.output
    assert "snapshot hashes" in result.output
    assert (out / "fetch_report.json").exists()


def test_cli_fetch_reports_data_source_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    universe = tmp_path / "universe.yaml"
    universe.write_text("universe: T\nversion: 1\nsymbols: [SPY]\n")
    monkeypatch.setattr(
        cli_main,
        "AlpacaDataClient",
        lambda key, secret: AlpacaDataClient(
            key, secret, transport=FakeTransport([status(403, "plan")])
        ),
    )
    result = run(
        "--end", "2024-07-02", "--universe", str(universe), "--out-dir", str(tmp_path), env=KEYS
    )
    assert result.exit_code == 4
    assert "DATA-3" in result.output
