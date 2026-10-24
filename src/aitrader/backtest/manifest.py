"""Run manifest and result files (document 12 §7). Same inputs → byte-identical files."""

import csv
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import yaml

from aitrader.backtest.engine import BacktestResult
from aitrader.config import Settings, config_hash
from aitrader.data import CorporateActionKind, DailyBarSource, PointInTimeBars
from aitrader.fees import WEBULL_JP, ExecutionAssumptions
from aitrader.metrics import buy_and_hold, fmt, summarize
from aitrader.strategy import Strategy, params_hash

BENCHMARK = "SPY"


def build_manifest(
    *,
    settings: Settings,
    strategy: Strategy,
    source: DailyBarSource,
    start: date,
    end: date,
    git_commit: str,
    variants_tried: int,
    holdout_used: bool = False,
    adjusted: DailyBarSource | None = None,
) -> dict[str, object]:
    pit = PointInTimeBars(source, adjusted)
    manifest: dict[str, object] = {
        "git_commit": git_commit,
        "config_hash": config_hash(settings),
        "strategy": {
            "id": strategy.strategy_id,
            "version": strategy.version,
            "params_hash": params_hash(strategy),
        },
        "data_snapshot_hash": source.snapshot_hash,
        "signal_data_snapshot_hash": pit.signal_snapshot_hash,
        "corporate_actions": {
            kind.value: sum(
                1
                for symbol in pit.symbols
                for action in pit.actions(symbol)
                if start <= action.session <= end and action.kind is kind
            )
            for kind in CorporateActionKind
        },
        "ai_replay_store_hash": None,
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "holdout_used": holdout_used,
        },
        "cost_profile": WEBULL_JP.name,
        "random_seed": 0,
        "variants_tried_so_far": variants_tried,
    }
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest()
    return {"run_id": f"bt-{digest[:16]}", **manifest}


def metrics_for(
    result: BacktestResult, source: DailyBarSource, settings: Settings
) -> dict[str, object]:
    bt = settings.require_backtest()
    sessions = {p.session for p in result.points}
    benchmark_bars = [b for b in source.bars(BENCHMARK) if b.ts.date() in sessions]
    prices = {}
    for symbol in result.ledger.positions:
        bars = [b for b in source.bars(symbol) if b.ts.date() <= result.points[-1].session]
        prices[symbol] = bars[-1].close
    return summarize(
        start_equity=result.start_equity_usd,
        points=result.points,
        ledger=result.ledger,
        fx_jpy_per_usd=bt.fx_jpy_per_usd,
        traded_notional_usd=result.traded_notional_usd,
        spread_and_slippage_usd=result.spread_and_slippage_usd,
        prices=prices,
        benchmark_symbol=BENCHMARK,
        benchmark=buy_and_hold(
            benchmark_bars,
            result.start_equity_usd,
            WEBULL_JP,
            ExecutionAssumptions(),
            distributions={
                a.session: a.cash_per_share
                for a in (
                    result.corporate_actions.actions(BENCHMARK)
                    if result.corporate_actions is not None
                    else ()
                )
                if a.kind is CorporateActionKind.DISTRIBUTION
            },
            dividend_us_withholding_pct=bt.dividend_us_withholding_pct,
            dividend_jp_withholding_pct=bt.dividend_jp_withholding_pct,
        ),
    )


def equity_csv(result: BacktestResult) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["session", "equity_usd", "market_value_usd"])
    for point in result.points:
        writer.writerow(
            [point.session.isoformat(), fmt(point.equity_usd, 6), fmt(point.market_value_usd, 6)]
        )
    return buffer.getvalue()


def write_results(
    out_dir: Path, manifest: dict[str, object], metrics: dict[str, object], result: BacktestResult
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "equity.csv").write_text(equity_csv(result), encoding="utf-8")
