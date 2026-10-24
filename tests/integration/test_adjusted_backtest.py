"""Raw execution, dividend accounting, action guards and deterministic dual-series CLI runs."""

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from aitrader.backtest import build_manifest, metrics_for, run_backtest
from aitrader.data import CorporateActionError, DailyBarSource, DataQualityGate
from aitrader.domain import Currency, Money, OrderIntent
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import buy_and_hold
from aitrader.portfolio import Account, EntryKind
from aitrader.reconcile import Tolerances, compare_cash
from aitrader.strategy import STRATEGIES, StrategyContext
from tests.integration.test_backtest import Scripted as BaseScripted
from tests.integration.test_backtest import cli, settings
from tests.unit.data.adjusted_builders import CAL
from tests.unit.data.adjusted_builders import sources as make_sources

D = Decimal
DAYS = CAL.sessions(date(2023, 1, 3), date(2023, 6, 9))
SIGNAL = date(2023, 6, 1)
EX = date(2023, 6, 6)
END = DAYS[-1]


def sources(
    root: Path,
    sessions: list[date],
    closes: list[Decimal],
    factors: Mapping[date, Decimal],
) -> tuple[DailyBarSource, DailyBarSource]:
    # Nonzero historical volatility is required by the existing risk engine.
    prices = [
        c + (D("0.01") if i % 2 and day < SIGNAL else 0)
        for i, (day, c) in enumerate(zip(sessions, closes, strict=True))
    ]
    return make_sources(root, sessions, prices, factors)


class Scripted(BaseScripted):
    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
        return [
            intent.model_copy(update={"target_notional": Money.of(40, Currency.USD)})
            for intent in super().evaluate(ctx)
        ]


class Recorder:
    strategy_id = "REC"
    version = "1"

    def __init__(self) -> None:
        self.contexts: list[StrategyContext] = []

    def params(self) -> Mapping[str, str]:
        return {}

    def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
        self.contexts.append(ctx)
        return []


def test_engine_dividend_cash_taxes_raw_marks_and_benchmark(tmp_path: Path) -> None:
    raw, adjusted = sources(
        tmp_path, DAYS, [D(100) if d < EX else D(99) for d in DAYS], {EX: D("0.99")}
    )
    result = run_backtest(
        settings(),
        Scripted(SIGNAL, "3"),
        raw,
        adjusted=adjusted,
        start=SIGNAL,
        end=END,
        calendar=CAL,
    )
    (buy,) = result.fills
    assert buy.fill.price == D("100.05")  # raw ask and slippage, not adjusted 99
    held = result.ledger.position("SPY")
    assert held is not None
    assert held.quantity == D("0.39980")
    dividends = [e for e in result.ledger.entries if e.kind is EntryKind.DIVIDEND]
    (entry,) = dividends
    assert entry.entry_id == "div-SPY-2023-06-06"
    assert entry.ts == CAL.session_open(EX)
    # 0.39980 shares * $1 = $0.40 gross, $0.04 US, $0.07 JP, $0.29 net.
    assert entry.total(Account.DIVIDEND_INCOME, Currency.USD) == D("-0.40")
    taxes = {
        p.detail: p.amount.amount for p in entry.postings if p.account is Account.WITHHOLDING_TAX
    }
    assert taxes == {"US": D("0.04"), "JP": D("0.07")}
    assert entry.total(Account.CASH_USD_SETTLED, Currency.USD) == D("0.29")
    assert entry == next(e for e in result.broker.ledger.entries if e.kind is EntryKind.DIVIDEND)
    assert result.ledger.cash_usd_settled == result.broker.ledger.cash_usd_settled
    assert compare_cash(result.ledger, result.broker.account(), Tolerances()) == []
    previous = result.points[1]
    current = next(p for p in result.points if p.session == EX)
    assert current.equity_usd - previous.equity_usd == D("0.29") - held.quantity
    assert current.market_value_usd == held.quantity * 99
    assert result.cycle.stops["SPY"] == D(97)  # ex-date does not adjust protective stops
    metrics = metrics_for(result, raw, settings())
    pnl = metrics["pnl"]
    assert isinstance(pnl, dict)
    assert pnl["dividends_gross_usd"] == "0.400000"
    assert pnl["withholding_tax_usd"] == "0.110000"
    bt = settings().require_backtest()
    benchmark = buy_and_hold(
        [b for b in raw.bars("SPY") if b.ts.date() >= SIGNAL],
        D(323),
        WEBULL_JP,
        ExecutionAssumptions(),
        distributions={EX: D(1)},
        dividend_us_withholding_pct=bt.dividend_us_withholding_pct,
        dividend_jp_withholding_pct=bt.dividend_jp_withholding_pct,
    )
    benchmarks = metrics["benchmarks"]
    assert isinstance(benchmarks, dict)
    assert benchmarks["B0_buy_and_hold"]["total_pct"] == format(
        ((benchmark[-1].equity_usd / 323 - 1) * 100).quantize(D("0.0001")), "f"
    )


@pytest.mark.parametrize("signal", [None, date(2023, 6, 5)])
def test_no_dividend_if_not_held_at_previous_close(tmp_path: Path, signal: date | None) -> None:
    raw, adjusted = sources(tmp_path, DAYS, [D(100)] * len(DAYS), {EX: D("0.99")})
    strategy = Scripted(signal, "3") if signal is not None else Recorder()
    result = run_backtest(
        settings(), strategy, raw, adjusted=adjusted, start=SIGNAL, end=END, calendar=CAL
    )
    assert not any(e.kind is EntryKind.DIVIDEND for e in result.ledger.entries)
    assert not any(e.kind is EntryKind.DIVIDEND for e in result.broker.ledger.entries)
    assert bool(result.fills) == (signal is not None)


def test_zero_rounded_gross_is_not_booked(tmp_path: Path) -> None:
    # $1000 price => 0.04 shares; >0.05% ratio change => $0.60/share; gross still >0.
    # Instead buy a very small position in the normal-price synthetic ETF.
    raw, adjusted = sources(tmp_path, DAYS, [D(100)] * len(DAYS), {EX: D("0.9994")})

    class Tiny(Scripted):
        def evaluate(self, ctx: StrategyContext) -> list[OrderIntent]:
            return [
                intent.model_copy(update={"target_notional": Money.of(5, Currency.USD)})
                for intent in super().evaluate(ctx)
            ]

    result = run_backtest(
        settings(), Tiny(SIGNAL, "3"), raw, adjusted=adjusted, start=SIGNAL, end=END, calendar=CAL
    )
    assert result.ledger.position("SPY") is not None
    assert not any(e.kind is EntryKind.DIVIDEND for e in result.ledger.entries)


@pytest.mark.parametrize(
    ("factor", "kind"),
    [
        ("0.5", "split"),
        ("2", "split"),
        ("1.01", "unexplained"),
        ("0.6180", "unexplained"),
        ("0.7310", "unexplained"),
    ],
)
@pytest.mark.parametrize("signal", [SIGNAL, date(2023, 6, 5)])
def test_held_and_pending_unsupported_actions_fail_closed(
    tmp_path: Path,
    factor: str,
    kind: str,
    signal: date,
) -> None:
    raw, adjusted = sources(tmp_path, DAYS, [D(100)] * len(DAYS), {EX: D(factor)})
    with pytest.raises(CorporateActionError, match=rf"SPY {EX}: {kind} factor="):
        run_backtest(
            settings(),
            Scripted(signal, "3"),
            raw,
            adjusted=adjusted,
            start=SIGNAL,
            end=END,
            calendar=CAL,
        )


def test_unheld_split_adjusts_signals_and_volume_without_dq3(tmp_path: Path) -> None:
    raw, adjusted = sources(
        tmp_path, DAYS, [D(100) if d < EX else D(50) for d in DAYS], {EX: D("0.5")}
    )
    strategy = Recorder()
    result = run_backtest(
        settings(), strategy, raw, adjusted=adjusted, start=SIGNAL, end=END, calendar=CAL
    )
    history = next(c.history["SPY"] for c in strategy.contexts if c.session == EX)
    assert history[-2].close == history[-1].close == D(50)
    assert history[-2].volume == D(2_000_002)
    assert history[-1].volume == D(1_000_001)
    assert "DQ-3" not in DataQualityGate(CAL).findings(history, date(2023, 6, 7))
    assert result.points[-1].equity_usd == D(323)
    assert all(p.market_value_usd == 0 for p in result.points)


@pytest.mark.parametrize("present", ["raw", "adjusted"])
def test_cli_requires_both_directories(tmp_path: Path, present: str) -> None:
    (tmp_path / present).mkdir()
    result = cli("backtest", "--data-dir", str(tmp_path), "--out-dir", str(tmp_path / "out"))
    assert result.exit_code == 4
    missing = "adjusted/" if present == "raw" else "raw/"
    assert f"missing {missing}" in result.output


def test_cli_split_error_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources(tmp_path, DAYS, [D(100)] * len(DAYS), {EX: D("0.5")})
    monkeypatch.setitem(STRATEGIES, "TEST", lambda: Scripted(SIGNAL, "3"))
    result = cli(
        "backtest",
        "--data-dir",
        str(tmp_path),
        "--out-dir",
        str(tmp_path / "out"),
        "--strategy",
        "TEST",
        "--start",
        SIGNAL.isoformat(),
        "--end",
        END.isoformat(),
    )
    assert result.exit_code == 4
    assert f"SPY {EX}: split factor=0.5" in result.output
    assert not (tmp_path / "out").exists()


def test_dual_series_cli_is_reproducible_and_manifest_counts_period_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, adjusted = sources(
        tmp_path,
        DAYS,
        [D(100)] * len(DAYS),
        {DAYS[1]: D("0.5"), EX: D("0.99"), DAYS[-1]: D("1.01")},
    )
    monkeypatch.setitem(STRATEGIES, "TEST", Recorder)
    for name in ("a", "b"):
        result = cli(
            "backtest",
            "--data-dir",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / name),
            "--strategy",
            "TEST",
            "--start",
            SIGNAL.isoformat(),
            "--end",
            END.isoformat(),
        )
        assert result.exit_code == 0, result.output
    for name in ("manifest.yaml", "metrics.json", "equity.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    manifest = yaml.safe_load((tmp_path / "a" / "manifest.yaml").read_text())
    assert manifest["data_snapshot_hash"] == raw.snapshot_hash
    assert manifest["signal_data_snapshot_hash"] == adjusted.snapshot_hash
    assert manifest["corporate_actions"] == {"split": 0, "distribution": 1, "unexplained": 1}
    assert "dividends_gross_usd" in json.loads((tmp_path / "a" / "metrics.json").read_text())["pnl"]
    flat = build_manifest(
        settings=settings(),
        strategy=Recorder(),
        source=raw,
        start=SIGNAL,
        end=END,
        git_commit="test",
        variants_tried=1,
    )
    assert flat["signal_data_snapshot_hash"] is None
    assert flat["corporate_actions"] == {"split": 0, "distribution": 0, "unexplained": 0}


@pytest.mark.parametrize("field", ["dividend_us_withholding_pct", "dividend_jp_withholding_pct"])
@pytest.mark.parametrize("value", ["-0.0001", "0", "0.0001", "99.9999", "100", "100.0001"])
def test_withholding_settings_boundaries(field: str, value: str) -> None:
    from pydantic import ValidationError

    from aitrader.config.settings import BacktestSettings

    payload = settings().require_backtest().model_dump()
    payload[field] = D(value)
    if D(0) <= D(value) <= D(100):
        configured = BacktestSettings.model_validate(payload)
        assert getattr(configured, field) == D(value)
    else:
        with pytest.raises(ValidationError, match=field):
            BacktestSettings.model_validate(payload)
