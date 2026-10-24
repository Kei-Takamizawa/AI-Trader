"""Action thresholds, segment caching, and point-in-time invariants."""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aitrader.data import (
    DailyBarSource,
    DataError,
    PointInTimeBars,
    detect_corporate_actions,
)
from aitrader.data.ingest import corporate_actions
from aitrader.domain import Bar
from tests.unit.data.adjusted_builders import CAL, sources
from tests.unit.data.test_ingest import row

D = Decimal
DAYS = CAL.sessions(date(2024, 7, 1), date(2024, 7, 12))


@pytest.mark.parametrize(
    ("factor", "kind"),
    [
        ("0.999501", None),
        ("0.9995", None),
        ("0.999499", "distribution"),
        ("1.000499", None),
        ("1.0005", None),
        ("1.000501", "unexplained"),
        ("0.800001", "distribution"),
        ("0.8", "distribution"),
        ("0.799999", "split"),
        ("1.199999", "unexplained"),
        ("1.2", "unexplained"),
        ("1.200001", "split"),
        ("0.99", "distribution"),
        ("2", "split"),
    ],
)
def test_detection_thresholds_and_ingest_share_one_implementation(
    factor: str,
    kind: str | None,
) -> None:
    f = D(factor)
    # Ratios are exactly 1 and f: avoids artificial division error at boundaries.
    raw = [(DAYS[0], D(100)), (DAYS[1], D(100) * f)]
    adjusted = [(DAYS[0], D(100)), (DAYS[1], D(100))]
    actions = detect_corporate_actions("SPY", raw, adjusted)
    report = corporate_actions(
        [row(d, str(c)) for d, c in raw], [row(d, str(c)) for d, c in adjusted]
    )
    if kind is None:
        assert actions == ()
        assert report == []
    else:
        (action,) = actions
        assert action.kind == kind
        assert action.factor == f
        assert report == [(DAYS[1], kind, f)]
        assert action.cash_per_share == (
            (100 * (1 - f)).quantize(D("0.000001"), rounding=ROUND_HALF_EVEN)
            if kind == "distribution"
            else 0
        )
        if factor == "0.99":
            assert action.cash_per_share == D("1.000000")


def test_history_boundaries_and_cache(tmp_path: Path) -> None:
    raw, adjusted = sources(
        tmp_path,
        DAYS,
        [D(100)] * len(DAYS),
        {
            DAYS[2]: D("0.99"),
            DAYS[5]: D("0.5"),
        },
    )
    pit = PointInTimeBars(raw, adjusted)
    assert pit.symbols == raw.symbols
    assert pit.snapshot_hash == raw.snapshot_hash
    assert pit.signal_snapshot_hash == adjusted.snapshot_hash
    assert pit.actions("MISSING") == ()
    assert pit.actions_on(DAYS[1]) == ()
    assert pit.history("MISSING", CAL.session_close(DAYS[0])) == ()
    assert pit.history("SPY", CAL.session_open(DAYS[0])) == ()
    assert pit.history("SPY", CAL.session_open(DAYS[2])) == raw.bars("SPY")[:2]
    first = pit.history("SPY", CAL.session_close(DAYS[2]))
    assert first[0].close == D(99)
    assert first[0].volume == D(1_000_001)  # dividends never change volume
    with patch.object(Bar, "model_copy", side_effect=RuntimeError("rebuilt")):
        later = pit.history("SPY", CAL.session_close(DAYS[4]))
    assert later[0] is first[0]
    assert later[2:] == raw.bars("SPY")[2:5]  # no rounding after latest action
    split = pit.history("SPY", CAL.session_close(DAYS[5]))
    assert split[0].close == D("49.5")
    assert split[0].volume == D(2_000_002)
    assert split[-1] == raw.bars("SPY")[5]
    plain = PointInTimeBars(raw, None)
    assert plain.signal_snapshot_hash is None
    assert plain.actions("SPY") == ()
    assert plain.history("SPY", CAL.session_close(DAYS[-1])) == raw.bars("SPY")


@pytest.mark.parametrize("side", ["raw", "adjusted"])
@pytest.mark.parametrize("difference", ["symbol", "first", "last", "empty"])
def test_sources_must_match(tmp_path: Path, side: str, difference: str) -> None:
    sources(tmp_path, DAYS[:3], [D(100)] * 3, {})
    path = tmp_path / side / "SPY.csv"
    lines = path.read_text().splitlines()
    if difference == "symbol":
        path.rename(path.with_name("QQQ.csv"))
    elif difference == "empty":
        path.write_text(lines[0] + "\n")
    else:
        lines.pop(1 if difference == "first" else -1)
        path.write_text("\n".join(lines) + "\n")
    raw = DailyBarSource(tmp_path / "raw", CAL)
    adjusted = DailyBarSource(tmp_path / "adjusted", CAL)
    with pytest.raises(DataError, match=r"(SPY|QQQ).*2024-07"):
        PointInTimeBars(raw, adjusted)


def test_missing_symbol_with_empty_series(tmp_path: Path) -> None:
    sources(tmp_path, [], [], {})
    (tmp_path / "raw" / "SPY.csv").rename(tmp_path / "raw" / "QQQ.csv")
    with pytest.raises(DataError, match=r"QQQ.*session: none"):
        PointInTimeBars(
            DailyBarSource(tmp_path / "raw", CAL), DailyBarSource(tmp_path / "adjusted", CAL)
        )


@settings(max_examples=60, deadline=None)
@given(
    closes=st.lists(st.integers(80, 200), min_size=8, max_size=8),
    factors=st.lists(st.sampled_from(["1", "0.98", "0.99", "0.5", "2"]), min_size=7, max_size=7),
)
def test_no_lookahead_across_vendor_download_dates(
    closes: list[int],
    factors: list[str],
) -> None:
    days = DAYS[:8]
    events = {day: D(factor) for day, factor in zip(days[1:], factors, strict=True)}
    prices = [D(c) for c in closes]
    # Test every possible cutoff, including dates before any action and after the last.
    with TemporaryDirectory(dir=".pytest_cache") as directory:
        root = Path(directory)
        raw, adjusted = sources(root / "full", days, prices, events, volume=D(1000))
        full = PointInTimeBars(raw, adjusted)
        for t, session in enumerate(days):
            # Only actions up to the download date can appear in the vendor snapshot.
            partial_raw, partial_adjusted = sources(
                root / f"partial-{t}",
                days[: t + 1],
                prices[: t + 1],
                {d: f for d, f in events.items() if d <= session},
                volume=D(1000),
            )
            pit = full.history("SPY", CAL.session_close(session))
            partial = PointInTimeBars(partial_raw, partial_adjusted).history(
                "SPY", CAL.session_close(session)
            )
            assert pit[-1] == raw.bars("SPY")[t]
            for i, (actual, earlier) in enumerate(zip(pit, partial, strict=True)):
                expected_factor = D(1)
                expected_split = D(1)
                for day, factor in events.items():
                    if days[i] < day <= session:
                        expected_factor *= factor
                        if abs(factor - 1) > D("0.2"):
                            expected_split *= factor
                for name in ("open", "high", "low", "close"):
                    expected = getattr(raw.bars("SPY")[i], name) * expected_factor
                    assert abs(getattr(actual, name) / expected - 1) <= D("0.0001")
                    assert abs(getattr(actual, name) / getattr(earlier, name) - 1) <= D("0.0001")
                expected_volume = (raw.bars("SPY")[i].volume / expected_split).quantize(
                    D(1), rounding=ROUND_HALF_EVEN
                )
                assert actual.volume == expected_volume
                assert actual.low <= min(actual.open, actual.close)
                assert actual.high >= max(actual.open, actual.close)
                Bar.model_validate(actual.model_dump())


def test_actions_on_sorted_and_split_volume_half_even(tmp_path: Path) -> None:
    raw, adjusted = sources(tmp_path, DAYS[:2], [D(100), D(200)], {DAYS[1]: D(2)})
    for directory in ("raw", "adjusted"):
        path = tmp_path / directory / "SPY.csv"
        path.with_name("QQQ.csv").write_bytes(path.read_bytes())
    raw, adjusted = (
        DailyBarSource(tmp_path / "raw", CAL),
        DailyBarSource(tmp_path / "adjusted", CAL),
    )
    pit = PointInTimeBars(raw, adjusted)
    assert [a.symbol for a in pit.actions_on(DAYS[1])] == ["QQQ", "SPY"]
    assert pit.history("SPY", CAL.session_close(DAYS[1]))[0].volume == D(500_000)


@pytest.mark.parametrize(("close", "cash"), [("100.00005", "1.000000"), ("100.00015", "1.000002")])
def test_distribution_estimate_half_even(close: str, cash: str) -> None:
    events = detect_corporate_actions(
        "SPY", [(DAYS[0], D(close)), (DAYS[1], D(99))], [(DAYS[0], D(close)), (DAYS[1], D(100))]
    )
    assert events[0].cash_per_share == D(cash)
