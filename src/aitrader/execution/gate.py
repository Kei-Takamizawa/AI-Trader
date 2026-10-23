"""The order gate: the last check before any broker call (document 05 §13).

It re-checks the hard caps independently of the risk engine, using the smaller of the
configured limit and the compiled-in hard cap. Every rule is evaluated and recorded. Exits that
reduce risk are never blocked by the buying-power and exposure rules, and protective exits
(stop exits, flatten) are not counted against the daily order cap.
"""

from collections.abc import Mapping
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from aitrader.broker.port import BrokerCapabilities
from aitrader.config import HARD_CAPS, RiskLimits
from aitrader.domain import (
    Instrument,
    IntentPurpose,
    OrderRequest,
    OrderType,
    RiskDecision,
    RuleResult,
    Side,
    SystemState,
    TimeInForce,
    Verdict,
)
from aitrader.domain.types import NonNegativeDecimal, PositiveDecimal, UtcDatetime
from aitrader.execution.store import OrderRecord

HUNDRED = Decimal(100)
PROTECTIVE = frozenset({IntentPurpose.STOP_EXIT, IntentPurpose.FLATTEN})


class GateContext(BaseModel):
    """Account and system facts at the moment of the broker call. Amounts in USD."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    now: UtcDatetime
    system_state: SystemState
    kill_switch: bool = False
    market_open: bool
    fx_jpy_per_usd: PositiveDecimal
    equity_usd: PositiveDecimal
    settled_cash_usd: NonNegativeDecimal
    holdings_value_usd: NonNegativeDecimal
    held_quantity: Mapping[str, Decimal] = Field(default_factory=dict)
    open_orders: tuple[OrderRecord, ...] = ()
    orders_today: int = Field(default=0, ge=0)
    allow_list: frozenset[str] = frozenset()
    capabilities: BrokerCapabilities


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str
    results: tuple[RuleResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed_rules(self) -> tuple[str, ...]:
        return tuple(r.rule_id for r in self.results if not r.passed)


class GateRejectedError(Exception):
    def __init__(self, result: GateResult) -> None:
        super().__init__(f"order gate refused {result.client_order_id}: {result.failed_rules}")
        self.result = result


class OrderGate:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def _cap(self, name: str) -> Decimal:
        configured: Decimal = getattr(self.limits, name)
        return min(configured, HARD_CAPS[name])

    def enforce(
        self,
        request: OrderRequest,
        decision: RiskDecision,
        instrument: Instrument,
        ctx: GateContext,
    ) -> GateResult:
        result = self.check(request, decision, instrument, ctx)
        if not result.passed:
            raise GateRejectedError(result)
        return result

    def check(
        self,
        request: OrderRequest,
        decision: RiskDecision,
        instrument: Instrument,
        ctx: GateContext,
    ) -> GateResult:
        results: list[RuleResult] = []

        def add(rule_id: str, passed: bool, detail: str, **values: object) -> None:
            results.append(
                RuleResult(
                    rule_id=rule_id,
                    passed=passed,
                    detail=detail,
                    values={k: str(v) for k, v in values.items()},
                )
            )

        buy = request.side is Side.BUY
        protective = request.purpose in PROTECTIVE

        add(
            "HC-8",
            decision.intent_id == request.intent_id
            and decision.verdict is not Verdict.REJECT
            and decision.approved_quantity == request.quantity,
            "the order matches an approved risk decision for the same intent and quantity",
            verdict=decision.verdict.value,
            approved=decision.approved_quantity,
            quantity=request.quantity,
        )
        add(
            "DUP-1",
            all(o.intent_id != request.intent_id for o in ctx.open_orders if o.is_active),
            "one active order per intent",
        )

        killed = ctx.kill_switch or ctx.system_state is SystemState.KILLED
        if buy:
            state_ok = ctx.system_state is SystemState.RUNNING and not ctx.kill_switch
        elif killed:
            state_ok = protective
        else:
            state_ok = ctx.system_state is not SystemState.SAFE
        add(
            "HC-9",
            state_ok,
            "system state and kill switch allow this order",
            state=ctx.system_state.value,
            kill_switch=ctx.kill_switch,
            purpose=request.purpose.value,
        )
        add(
            "HC-7",
            ctx.market_open and not request.extended_hours,
            "regular trading hours only",
            market_open=ctx.market_open,
        )
        add(
            "HC-3",
            not buy or (request.symbol in ctx.allow_list and instrument.symbol == request.symbol),
            "buys only in allow-listed instruments",
        )

        held = ctx.held_quantity.get(request.symbol, Decimal(0))
        selling = sum(
            (
                o.remaining
                for o in ctx.open_orders
                if o.is_active
                and o.request.side is Side.SELL
                and o.request.symbol == request.symbol
            ),
            Decimal(0),
        )
        add(
            "HC-1",
            buy or request.quantity <= held - selling,
            "a sell never exceeds the held quantity not already being sold (no short selling)",
            held=held,
            open_sells=selling,
        )

        hard_orders = int(HARD_CAPS["max_orders_per_day"])
        add(
            "HC-6",
            protective or ctx.orders_today < hard_orders,
            "orders per day (protective exits are never blocked)",
            orders_today=ctx.orders_today,
            cap=hard_orders,
        )

        increment = (
            ctx.capabilities.fractional_step
            if instrument.fractionable and ctx.capabilities.fractional_orders
            else instrument.quantity_increment
        )
        type_ok = request.order_type in ctx.capabilities.order_types and (
            request.order_type is OrderType.LIMIT
            or (request.order_type is OrderType.MARKET and protective)
        )
        fractional_buy = buy and request.quantity % 1 != 0
        notional = request.notional_at_limit
        minimum_ok = not fractional_buy or (
            notional is not None and notional >= ctx.capabilities.min_fractional_buy_usd
        )
        add(
            "HC-10",
            type_ok
            and request.time_in_force is TimeInForce.DAY
            and request.quantity % increment == 0
            and minimum_ok,
            "fixed order fields: limit orders (market only for protective exits), DAY, broker "
            "increment and minimum",
            order_type=request.order_type.value,
            increment=increment,
        )

        if buy:
            reserved = sum(
                (
                    o.remaining * (o.request.limit_price or Decimal(0))
                    for o in ctx.open_orders
                    if o.is_active and o.request.side is Side.BUY
                ),
                Decimal(0),
            )
            need = notional
            fx = ctx.fx_jpy_per_usd
            if need is None:
                for rule_id in ("HC-2", "HC-4", "HC-5", "HC-11"):
                    add(rule_id, False, "a buy needs a limit price to be checked")
            else:
                total = ctx.holdings_value_usd + reserved + need
                gross_cap = ctx.equity_usd * self._cap("max_gross_exposure_pct") / HUNDRED
                add(
                    "HC-2",
                    need + reserved <= ctx.settled_cash_usd and total <= gross_cap,
                    "no leverage: settled cash pays for every open buy; exposure within equity",
                    need_usd=need,
                    reserved_usd=reserved,
                    settled_cash_usd=ctx.settled_cash_usd,
                )
                add(
                    "HC-4",
                    need * fx <= self._cap("live_max_order_notional_jpy"),
                    "order notional cap",
                    notional_jpy=need * fx,
                )
                add(
                    "HC-5",
                    total * fx <= self._cap("live_max_total_holdings_jpy"),
                    "total holdings cap",
                    holdings_jpy=total * fx,
                )
                add(
                    "HC-11",
                    total * fx <= self._cap("approved_capital_jpy"),
                    "never more than the approved capital in the market",
                    holdings_jpy=total * fx,
                )
        else:
            for rule_id in ("HC-2", "HC-4", "HC-5", "HC-11"):
                add(rule_id, True, "not applicable to a sell")

        return GateResult(client_order_id=request.client_order_id, results=tuple(results))
