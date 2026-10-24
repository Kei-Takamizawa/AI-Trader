"""The risk engine: one deterministic decision per order intent (document 05).

Every rule is evaluated and recorded, even after another rule has failed, so the audit trail is
complete. The engine never raises a quantity above what the strategy asked for, and AI
advisories can only veto or shrink an entry.
"""

import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal

from aitrader.config import RiskLimits
from aitrader.domain import (
    AdvisoryKind,
    AdvisorySignal,
    InstrumentKind,
    IntentPurpose,
    OrderIntent,
    RiskDecision,
    RuleResult,
    Side,
    SystemState,
    Verdict,
)
from aitrader.fees import WEBULL_JP, ExecutionAssumptions, FeeSchedule, round_trip_cost
from aitrader.risk.parameters import RiskParameters
from aitrader.risk.sizing import (
    HUNDRED,
    risk_budget,
    risk_limited_quantity,
    round_down,
)
from aitrader.risk.state import DATA_GATE_RULES, RiskState, SymbolMarketState

BPS = Decimal(10_000)
_EXIT_IN_KILLED = frozenset({IntentPurpose.STOP_EXIT, IntentPurpose.FLATTEN})


def _fmt(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


class _Audit:
    def __init__(self) -> None:
        self.results: list[RuleResult] = []

    def add(self, rule_id: str, passed: bool, detail: str = "", **values: object) -> bool:
        self.results.append(
            RuleResult(
                rule_id=rule_id,
                passed=passed,
                detail=detail,
                values={k: _fmt(v) for k, v in values.items()},
            )
        )
        return passed

    @property
    def failed(self) -> bool:
        return any(not r.passed for r in self.results)


class RiskEngine:
    def __init__(
        self,
        limits: RiskLimits,
        parameters: RiskParameters | None = None,
        fees: FeeSchedule = WEBULL_JP,
        assumptions: ExecutionAssumptions | None = None,
    ) -> None:
        self.limits = limits
        self.parameters = parameters or RiskParameters()
        self.fees = fees
        self.assumptions = assumptions or ExecutionAssumptions()
        payload = {
            "limits": self.limits.model_dump(mode="json"),
            "parameters": self.parameters.model_dump(mode="json"),
            "fees": self.fees.model_dump(mode="json"),
            "assumptions": self.assumptions.model_dump(mode="json"),
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.limits_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ public

    def evaluate(
        self,
        intent: OrderIntent,
        state: RiskState,
        market: SymbolMarketState | None,
        advisories: Sequence[AdvisorySignal] = (),
    ) -> RiskDecision:
        if intent.side is Side.BUY:
            return self._evaluate_entry(intent, state, market, advisories)
        return self._evaluate_exit(intent, state, market)

    # ------------------------------------------------------------------ helpers

    def _decision(
        self, intent: OrderIntent, audit: _Audit, quantity: Decimal, requested: Decimal
    ) -> RiskDecision:
        if audit.failed or quantity <= 0:
            if not audit.failed:  # pragma: no cover - every zero quantity has a failed rule
                audit.add("SIZE-0", False, "quantity rounded to zero")
            return RiskDecision(
                intent_id=intent.intent_id,
                verdict=Verdict.REJECT,
                approved_quantity=Decimal(0),
                rule_results=tuple(audit.results),
                limits_hash=self.limits_hash,
            )
        verdict = Verdict.REDUCE if quantity < requested else Verdict.APPROVE
        return RiskDecision(
            intent_id=intent.intent_id,
            verdict=verdict,
            approved_quantity=quantity,
            rule_results=tuple(audit.results),
            limits_hash=self.limits_hash,
        )

    def _increment(self, market: SymbolMarketState) -> Decimal:
        if market.instrument.fractionable:
            return self.fees.fractional_quantity_step
        return market.instrument.quantity_increment

    @staticmethod
    def _active_advisories(
        intent: OrderIntent, state: RiskState, advisories: Sequence[AdvisorySignal]
    ) -> list[AdvisorySignal]:
        return [
            a
            for a in advisories
            if a.valid_from <= state.now < a.valid_until
            and (
                a.kind is AdvisoryKind.MARKET_REGIME
                or (a.kind is AdvisoryKind.SYMBOL_EVENT and a.symbol == intent.symbol)
            )
        ]

    # ------------------------------------------------------------------ entries

    def _evaluate_entry(
        self,
        intent: OrderIntent,
        state: RiskState,
        market: SymbolMarketState | None,
        advisories: Sequence[AdvisorySignal],
    ) -> RiskDecision:
        audit = _Audit()
        p = self.parameters
        lim = self.limits

        audit.add(
            "STATE-1",
            state.system_state is SystemState.RUNNING and not state.kill_switch,
            "new entries need state RUNNING and no kill switch",
            state=state.system_state.value,
            kill_switch=state.kill_switch,
        )

        if market is None or market.instrument.symbol != intent.symbol:
            audit.add("R-INST-1", False, "no market state for this symbol")
            return self._decision(intent, audit, Decimal(0), Decimal(1))

        instrument = market.instrument
        quote = market.quote
        entry_price = intent.limit_price or (quote.ask if quote else None)
        last_price = (quote.bid + quote.ask) / 2 if quote else None
        stop_price = intent.stop_price

        # --- instrument and event rules
        reference_price = entry_price or market.previous_close or Decimal(0)
        inst_ok = (
            market.allow_listed
            and market.tradable
            and (not p.etf_only or instrument.kind is InstrumentKind.ETF)
            and reference_price >= p.min_price_usd
            and market.avg_daily_dollar_volume_20d >= p.min_avg_dollar_volume_usd
        )
        audit.add(
            "R-INST-1",
            inst_ok,
            "allow-listed, tradable, ETF-only setting, price and dollar volume floors",
            allow_listed=market.allow_listed,
            tradable=market.tradable,
            kind=instrument.kind.value,
            price=reference_price,
            avg_dollar_volume=market.avg_daily_dollar_volume_20d,
        )
        today = state.now.date()
        in_blackout = False
        if instrument.kind is InstrumentKind.COMMON_STOCK:
            if market.next_earnings_date is not None:
                days = (market.next_earnings_date - today).days
                in_blackout |= 0 <= days <= p.earnings_blackout_calendar_days_before
            if market.last_earnings_date is not None:
                days = (today - market.last_earnings_date).days
                in_blackout |= 0 <= days <= p.earnings_blackout_calendar_days_after
        audit.add("R-EVT-1", not in_blackout, "no entries around earnings (single stocks)")

        # --- data quality
        if quote is None:
            audit.add("DQ-1", False, "no quote")
        else:
            age = state.now - quote.ts
            fresh = -p.max_quote_age <= age <= p.max_quote_age
            audit.add(
                "DQ-1", fresh, "quote must be recent", age_seconds=Decimal(age.total_seconds())
            )
        for rule_id in sorted(DATA_GATE_RULES):
            audit.add(
                rule_id,
                rule_id not in market.data_quality_failures,
                "finding of the data quality gate for this symbol",
            )
        audit.add(
            "DQ-6",
            state.market.clock_drift_seconds <= p.max_clock_drift_seconds,
            "clock drift",
            drift_seconds=state.market.clock_drift_seconds,
        )

        # --- circuit breakers
        if last_price is None or market.previous_close is None or market.atr14 is None:
            audit.add("CB-1", False, "price move cannot be checked without quote, close and ATR")
        else:
            move = abs(last_price - market.previous_close)
            move_pct = move / market.previous_close * HUNDRED
            move_atr = move / market.atr14
            audit.add(
                "CB-1",
                move_pct <= p.max_move_pct and move_atr <= p.max_move_atr_multiple,
                "abnormal move from the previous close",
                move_pct=move_pct,
                move_atr=move_atr,
            )
        bench = state.market.benchmark_change_pct
        audit.add(
            "CB-2",
            bench is not None and bench > -p.benchmark_drop_pct,
            "benchmark intraday drop",
            benchmark_change_pct=bench if bench is not None else "unknown",
        )
        audit.add(
            "CB-3",
            not market.trading_halted and not state.market.market_wide_halt,
            "trading halts",
        )
        if quote is None:
            audit.add("CB-4", False, "spread cannot be checked without a quote")
        else:
            mid = (quote.bid + quote.ask) / 2
            spread_bps = (quote.ask - quote.bid) / mid * BPS
            max_spread = (
                p.max_spread_bps_etf
                if instrument.kind is InstrumentKind.ETF
                else p.max_spread_bps_stock
            )
            audit.add("CB-4", spread_bps <= max_spread, "quoted spread", spread_bps=spread_bps)
        if market.first_30m_volume is not None and market.avg_first_30m_volume_20d:
            ratio = market.first_30m_volume / market.avg_first_30m_volume_20d
            audit.add(
                "CB-5",
                ratio <= p.max_opening_volume_multiple,
                "news-shock volume proxy",
                volume_ratio=ratio,
            )
        else:
            audit.add("CB-5", True, "not available yet (first 30 minutes incomplete)")

        # --- loss limits
        day_pct = (state.equity_usd / state.start_of_day_equity_usd - 1) * HUNDRED
        month_pct = (state.equity_usd / state.month_start_equity_usd - 1) * HUNDRED
        drawdown_pct = (1 - state.equity_usd / state.peak_equity_usd) * HUNDRED
        audit.add(
            "R-DAY-1", day_pct > -lim.daily_loss_limit_pct, "daily loss limit", day_pct=day_pct
        )
        soft_reached = month_pct <= -lim.monthly_soft_loss_pct
        audit.add(
            "R-MONTH-1",
            True,
            "soft monthly level halves the risk per trade",
            month_pct=month_pct,
            halved=soft_reached,
        )
        audit.add(
            "R-MONTH-2",
            month_pct > -lim.monthly_loss_limit_pct,
            "monthly loss limit",
            month_pct=month_pct,
        )
        audit.add(
            "R-DD-1",
            drawdown_pct < lim.max_drawdown_pct,
            "drawdown from the equity peak",
            drawdown_pct=drawdown_pct,
        )

        # --- counts and duplicates
        audit.add(
            "R-ORD-1",
            state.orders_today < lim.max_orders_per_day,
            "orders per day",
            orders_today=state.orders_today,
        )
        held = state.position(intent.symbol)
        if held is None:
            occupied = {pos.symbol for pos in state.positions if pos.quantity > 0} | {
                o.symbol for o in state.open_orders if o.side is Side.BUY
            }
            audit.add(
                "R-POS-1",
                len(occupied) < lim.max_open_positions,
                "maximum open positions",
                open_positions=len(occupied),
            )
        else:
            audit.add("R-POS-1", True, "adds to an existing position")
        audit.add(
            "DUP-1",
            all(o.intent_id != intent.intent_id for o in state.open_orders),
            "one active order per intent",
        )
        audit.add(
            "DUP-3",
            not any(o.symbol == intent.symbol and o.side is Side.BUY for o in state.open_orders),
            "no second open entry order for the same symbol",
        )
        audit.add(
            "DUP-4",
            state.non_protective_orders_today.get(intent.symbol, 0)
            < p.max_non_protective_orders_per_symbol_per_day,
            "non-protective orders per symbol per day",
        )

        # --- sizing
        if entry_price is None or stop_price is None or stop_price >= entry_price:
            audit.add(
                "R-TRADE-1", False, "entry price unknown, or no stop below the entry (STOP-1)"
            )
            return self._decision(intent, audit, Decimal(0), Decimal(1))

        increment = self._increment(market)
        fx = state.fx_jpy_per_usd
        budget = risk_budget(
            limits=lim,
            assumptions=self.assumptions,
            equity_usd=state.equity_usd,
            fx_jpy_per_usd=fx,
            entry_price=entry_price,
            stop_price=stop_price,
            soft_monthly_level_reached=soft_reached,
            soft_monthly_risk_factor=p.soft_monthly_risk_factor,
        )
        audit.add(
            "R-LIVE-3",
            True,
            "sizing equity capped at the approved capital",
            equity_usd=state.equity_usd,
            sizing_equity_usd=budget.sizing_equity_usd,
        )
        q_risk = risk_limited_quantity(
            budget=budget,
            schedule=self.fees,
            assumptions=self.assumptions,
            entry_price=entry_price,
            stop_price=stop_price,
            increment=increment,
            fx_jpy_per_usd=fx,
        )
        audit.add(
            "R-TRADE-1",
            q_risk > 0,
            "risk per trade including fees, slippage and gap allowance",
            budget_usd=budget.budget_usd,
            loss_per_unit_usd=budget.loss_per_unit_usd,
            max_quantity=q_risk,
        )

        requested = round_down(intent.quantity_at(entry_price), increment)
        equity = budget.sizing_equity_usd
        existing_mv = held.market_value_usd if held else Decimal(0)
        open_buy_reserved = sum(
            (o.reserved_notional_usd for o in state.open_orders if o.side is Side.BUY), Decimal(0)
        )
        exposure = sum((pos.market_value_usd for pos in state.positions), Decimal(0))
        exposure += open_buy_reserved
        unit_cash = entry_price * (1 + self.assumptions.slippage_bps_per_side / BPS)
        unit_cash += self.fees.cat_fee_per_share_usd

        caps: dict[str, Decimal] = {}
        pos_pct = (
            lim.max_position_pct_etf
            if instrument.kind is InstrumentKind.ETF
            else lim.max_position_pct_stock
        )
        caps["R-POS-2"] = (equity * pos_pct / HUNDRED - existing_mv) / entry_price
        caps["R-PORT-1"] = (equity * lim.max_gross_exposure_pct / HUNDRED - exposure) / entry_price
        usd_cash = state.settled_cash_usd - open_buy_reserved
        caps["R-ECON-3"] = usd_cash / unit_cash
        caps["R-PORT-2"] = (usd_cash - equity * lim.min_cash_buffer_pct / HUNDRED) / unit_cash
        if market.daily_volatility > 0:
            used_vol = sum(
                (pos.market_value_usd * pos.daily_volatility for pos in state.positions),
                Decimal(0),
            ) + sum(
                (
                    o.reserved_notional_usd * o.daily_volatility
                    for o in state.open_orders
                    if o.side is Side.BUY
                ),
                Decimal(0),
            )
            vol_budget = equity * lim.max_portfolio_daily_vol_pct / HUNDRED - used_vol
            caps["R-PORT-3"] = vol_budget / (market.daily_volatility * entry_price)
        else:
            caps["R-PORT-3"] = Decimal(0)  # unknown volatility fails closed
        caps["R-ORD-2"] = market.avg_daily_volume_20d * lim.max_order_adv_pct / HUNDRED
        caps["R-LIVE-1"] = lim.live_max_order_notional_jpy / fx / entry_price
        caps["R-LIVE-2"] = (lim.live_max_total_holdings_jpy / fx - exposure) / entry_price

        details = {
            "R-POS-2": "maximum position size",
            "R-PORT-1": "gross exposure (no leverage)",
            "R-ECON-3": "USD cash only; no implicit JPY conversion",
            "R-PORT-2": "minimum cash buffer",
            "R-PORT-3": "portfolio daily volatility (correlation 1 bound)",
            "R-ORD-2": "order size versus average daily volume",
            "R-LIVE-1": "maximum order notional",
            "R-LIVE-2": "maximum total holdings",
        }
        for rule_id, cap in caps.items():
            capped = round_down(cap, increment)
            audit.add(rule_id, capped > 0, details[rule_id], max_quantity=capped)

        quantity = min([requested, q_risk, *caps.values()])
        quantity = round_down(quantity, increment)

        # --- AI overlay: veto or shrink only
        active = self._active_advisories(intent, state, advisories)
        veto = any(a.veto for a in active)
        multiplier = min((a.size_multiplier for a in active), default=Decimal(1))
        if veto:
            multiplier = Decimal(0)
        audit.add(
            "AI-1",
            multiplier > 0,
            "AI may veto or shrink, never enlarge",
            multiplier=multiplier,
            advisories=",".join(a.advisory_id for a in active) or "none",
        )
        quantity = round_down(quantity * multiplier, increment)

        # --- minimum order and cost efficiency
        notional = quantity * entry_price
        min_notional = self.fees.min_fractional_buy_usd if instrument.fractionable else Decimal(0)
        audit.add(
            "R-ECON-2",
            quantity >= increment and notional >= min_notional,
            "broker minimum order; never rounded up",
            quantity=quantity,
            notional_usd=notional,
            min_notional_usd=min_notional,
        )
        if quantity > 0:
            cost = round_trip_cost(
                self.fees, self.assumptions, quantity, entry_price, stop_price, fx_jpy_per_usd=fx
            )
            share = cost / budget.budget_usd * HUNDRED
            audit.add(
                "R-ECON-1",
                share <= lim.max_cost_share_of_risk_budget_pct,
                "round-trip cost versus risk budget",
                round_trip_cost_usd=cost,
                cost_share_pct=share,
                total_risk_usd=quantity * budget.loss_per_unit_usd + cost,
            )
        else:
            audit.add("R-ECON-1", True, "not evaluated: zero quantity")

        return self._decision(intent, audit, quantity, requested)

    # ------------------------------------------------------------------ exits

    def _evaluate_exit(
        self, intent: OrderIntent, state: RiskState, market: SymbolMarketState | None
    ) -> RiskDecision:
        audit = _Audit()
        killed = state.system_state is SystemState.KILLED or state.kill_switch
        if state.system_state is SystemState.SAFE:
            allowed = False
        elif killed:
            allowed = intent.purpose in _EXIT_IN_KILLED
        else:
            allowed = True
        audit.add(
            "STATE-1",
            allowed,
            "exits wait for reconciliation (SAFE); after a kill only stop exits and flatten",
            state=state.system_state.value,
            kill_switch=state.kill_switch,
            purpose=intent.purpose.value,
        )
        held = state.position(intent.symbol)
        available = held.available_quantity if held else Decimal(0)
        audit.add(
            "HC-1", available > 0, "a SELL needs a long position (no short selling)", held=available
        )
        audit.add(
            "DUP-1",
            all(o.intent_id != intent.intent_id for o in state.open_orders),
            "one active order per intent",
        )
        audit.add(
            "DUP-3",
            not any(o.symbol == intent.symbol and o.side is Side.SELL for o in state.open_orders),
            "no second open exit order for the same symbol",
        )

        price = intent.limit_price or (
            market.quote.bid if market is not None and market.quote is not None else None
        )
        if price is None and held is not None and held.market_value_usd > 0:
            price = held.market_value_usd / held.quantity
        if intent.target_quantity is not None:
            requested = intent.target_quantity
        elif price is not None:
            requested = intent.quantity_at(price)
        else:
            audit.add("EXIT-QTY", False, "no price to convert the notional into a quantity")
            return self._decision(intent, audit, Decimal(0), Decimal(1))

        if requested >= available:
            # Sell everything that is available; asking for more is a reduction, never a short.
            quantity, asked = available, requested
        else:
            increment = (
                self._increment(market)
                if market is not None
                else self.fees.fractional_quantity_step
            )
            quantity = round_down(requested, increment)
            asked = quantity
        audit.add(
            "EXIT-QTY",
            quantity > 0,
            "never more than the available quantity",
            requested=requested,
            available=available,
            quantity=quantity,
        )
        return self._decision(intent, audit, quantity, asked)


__all__ = ["RiskEngine"]
