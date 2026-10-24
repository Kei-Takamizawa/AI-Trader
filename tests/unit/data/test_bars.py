"""CSV daily bars and the look-ahead guard (document 12 §2)."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from aitrader.clock import SimClock, TradingCalendar
from aitrader.data import DailyBarSource, DataError, LookAheadError, MarketDataView, session_of

CAL = TradingCalendar()
HEADER = "date,open,high,low,close,volume\n"


def write(directory: Path, symbol: str, rows: list[str]) -> None:
    (directory / f"{symbol}.csv").write_text(HEADER + "".join(r + "\n" for r in rows))


ROWS = [
    "2024-07-02,100,101,99,100.5,1000",
    "2024-07-03,100.5,102,100,101,1200",
    "2024-07-05,101,103,100.5,102,900",
]


def test_bars_are_loaded_with_session_open_timestamps(tmp_path: Path) -> None:
    write(tmp_path, "SPY", ROWS)
    source = DailyBarSource(tmp_path, CAL)
    bars = source.bars("SPY")
    assert source.symbols == ("SPY",)
    assert [session_of(b) for b in bars] == [date(2024, 7, 2), date(2024, 7, 3), date(2024, 7, 5)]
    assert bars[0].ts == CAL.session_open(date(2024, 7, 2))
    assert bars[0].source == "csv:SPY.csv"
    assert source.bar_on("SPY", date(2024, 7, 3)) == bars[1]
    assert source.bar_on("SPY", date(2024, 7, 4)) is None
    assert source.bar_on("QQQ", date(2024, 7, 3)) is None
    assert source.bars("QQQ") == ()


def test_history_contains_closed_sessions_only(tmp_path: Path) -> None:
    write(tmp_path, "SPY", ROWS)
    source = DailyBarSource(tmp_path, CAL)
    half_day_close = CAL.session_close(date(2024, 7, 3))  # 13:00 New York
    assert len(source.history("SPY", half_day_close - timedelta(seconds=1))) == 1
    assert len(source.history("SPY", half_day_close)) == 2
    assert source.history("QQQ", half_day_close) == ()


def test_view_refuses_bars_from_the_future(tmp_path: Path) -> None:
    write(tmp_path, "SPY", ROWS)
    source = DailyBarSource(tmp_path, CAL)
    clock = SimClock(CAL.session_open(date(2024, 7, 5)))
    view = MarketDataView(source, clock)
    assert len(view.history("SPY")) == 2
    assert view.bar("SPY", date(2024, 7, 3)).close == 101
    with pytest.raises(LookAheadError, match="has not closed"):
        view.bar("SPY", date(2024, 7, 5))
    with pytest.raises(DataError, match="no SPY bar"):
        view.bar("SPY", date(2024, 7, 4))


def test_snapshot_hash_identifies_the_exact_files(tmp_path: Path) -> None:
    write(tmp_path, "SPY", ROWS)
    first = DailyBarSource(tmp_path, CAL).snapshot_hash
    assert DailyBarSource(tmp_path, CAL).snapshot_hash == first
    write(tmp_path, "SPY", [*ROWS[:2], "2024-07-05,101,103,100.5,102.01,900"])
    assert DailyBarSource(tmp_path, CAL).snapshot_hash != first


def test_symbol_selection(tmp_path: Path) -> None:
    write(tmp_path, "SPY", ROWS)
    write(tmp_path, "QQQ", ROWS)
    assert DailyBarSource(tmp_path, CAL, ["QQQ"]).symbols == ("QQQ",)
    with pytest.raises(DataError, match="no data file for IWM"):
        DailyBarSource(tmp_path, CAL, ["IWM", "SPY"])


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (["2024-07-04,1,1,1,1,1"], "not a trading session"),
        ([ROWS[1], ROWS[0]], "strictly increasing"),
        ([ROWS[0], ROWS[0]], "strictly increasing"),
        (["2024-07-02,100,99,101,100,1"], "high must not be below low"),
        (["2024-07-02,1.5e999,1,1,1,1"], "line 2"),
        (["not-a-date,1,1,1,1,1"], "line 2"),
    ],
)
def test_malformed_data_is_refused(tmp_path: Path, rows: list[str], message: str) -> None:
    write(tmp_path, "SPY", rows)
    with pytest.raises(DataError, match=message):
        DailyBarSource(tmp_path, CAL)


def test_header_and_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="no CSV files"):
        DailyBarSource(tmp_path, CAL)
    (tmp_path / "SPY.csv").write_text("day,o,h,l,c,v\n")
    with pytest.raises(DataError, match="header"):
        DailyBarSource(tmp_path, CAL)
