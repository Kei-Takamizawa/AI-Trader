"""Builders for risk engine inputs. The defaults describe the worked example of document 05 §2:
JPY 50,000 at JPY 155 per USD, a liquid fractionable ETF at USD 100 and a stop 4% below entry.
With the defaults every rule passes and the risk budget limits the size (verdict REDUCE).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from aitrader.domain import (
    AdvisoryKind,
    AdvisorySignal,
    AdvisorySource,
    Currency,
    InstrumentKind,
    Mode,
    Money,
    OrderIntent,
    RiskDecision,
    RuleResult,
    Side,
    SystemState,
)
from aitrader.risk import (
    MarketWideState,
    OpenOrderSnapshot,
    PositionSnapshot,
    RiskState,
    SymbolMarketState,
)

D = Decimal
NOW = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)  # 11:00 in New York
FX = D(155)
EQUITY = D(50_000) / FX  # about USD 322.58
PRICE = D(100)


def market(*, price: Decimal = PRICE, symbol: str = "SPY", **overrides: Any) -> SymbolMarketState:
    """Market state with a 2 bp spread, a 0.5% move from the previous close and 0.33 ATR."""
    instrument = overrides.pop(
        "instrument",
        {"symbol": symbol, "kind": InstrumentKind.ETF, "fractionable": True},
    )
    fields: dict[str, Any] = {
        "instrument": instrument,
        "allow_listed": True,
        "quote": {
            "symbol": symbol,
            "ts": NOW,
            "bid": price * D("0.9999"),
            "ask": price * D("1.0001"),
            "bid_size": D(100),
            "ask_size": D(100),
        },
        "previous_close": price * D("0.995"),
        "atr14": price * D("0.015"),
        "avg_daily_volume_20d": D(80_000_000),
        "avg_daily_dollar_volume_20d": D(80_000_000) * price,
        "daily_volatility": D("0.011"),
    }
    fields.update(overrides)
    return SymbolMarketState.model_validate(fields)


def state(**overrides: Any) -> RiskState:
    fields: dict[str, Any] = {
        "now": NOW,
        "mode": Mode.PAPER,
        "system_state": SystemState.RUNNING,
        "fx_jpy_per_usd": FX,
        "equity_usd": EQUITY,
        "start_of_day_equity_usd": EQUITY,
        "month_start_equity_usd": EQUITY,
        "peak_equity_usd": EQUITY,
        "settled_cash_usd": EQUITY,
        "market": MarketWideState(benchmark_change_pct=D("-0.2")),
    }
    fields.update(overrides)
    return RiskState.model_validate(fields)


def entry(*, price: Decimal = PRICE, symbol: str = "SPY", **overrides: Any) -> OrderIntent:
    fields: dict[str, Any] = {
        "intent_id": "intent-buy-1",
        "strategy_id": "S1",
        "strategy_version": "1.0.0",
        "symbol": symbol,
        "side": Side.BUY,
        "target_notional": Money.of(price * D("0.8"), Currency.USD),
        "limit_price": price,
        "stop_price": price * D("0.96"),
        "reason": "trend filter and momentum rank",
        "signal_ts": NOW,
    }
    fields.update(overrides)
    return OrderIntent.model_validate(fields)


def exit_intent(**overrides: Any) -> OrderIntent:
    fields: dict[str, Any] = {
        "intent_id": "intent-sell-1",
        "strategy_id": "S1",
        "strategy_version": "1.0.0",
        "symbol": "SPY",
        "side": Side.SELL,
        "target_quantity": D("0.3"),
        "reason": "trend filter turned off",
        "signal_ts": NOW,
    }
    fields.update(overrides)
    return OrderIntent.model_validate(fields)


def position(**overrides: Any) -> PositionSnapshot:
    fields: dict[str, Any] = {
        "symbol": "SPY",
        "quantity": D("0.3"),
        "available_quantity": D("0.3"),
        "market_value_usd": D(30),
        "daily_volatility": D("0.011"),
    }
    fields.update(overrides)
    return PositionSnapshot.model_validate(fields)


def open_order(**overrides: Any) -> OpenOrderSnapshot:
    fields: dict[str, Any] = {
        "intent_id": "other-intent",
        "client_order_id": "ABC-1",
        "symbol": "QQQ",
        "side": Side.BUY,
        "purpose": "ENTRY",
        "remaining_quantity": D("0.1"),
        "reserved_notional_usd": D(10),
        "daily_volatility": D("0.013"),
    }
    fields.update(overrides)
    return OpenOrderSnapshot.model_validate(fields)


def advisory(**overrides: Any) -> AdvisorySignal:
    fields: dict[str, Any] = {
        "advisory_id": "adv-1",
        "kind": AdvisoryKind.MARKET_REGIME,
        "size_multiplier": D("0.5"),
        "valid_from": NOW - timedelta(hours=1),
        "valid_until": NOW + timedelta(hours=1),
        "source": AdvisorySource.SCHEDULED_TASK,
        "model_id": "model",
        "prompt_version": "p1",
        "input_bundle_hash": "hash",
    }
    fields.update(overrides)
    return AdvisorySignal.model_validate(fields)


def failed(decision: RiskDecision) -> set[str]:
    return {r.rule_id for r in decision.rule_results if not r.passed}


def rule(decision: RiskDecision, rule_id: str) -> RuleResult:
    matches = [r for r in decision.rule_results if r.rule_id == rule_id]
    assert len(matches) == 1, f"{rule_id} recorded {len(matches)} times"
    return matches[0]
