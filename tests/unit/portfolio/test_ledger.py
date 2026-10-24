"""Ledger recording, cash-account rules and valuation (TASK-003)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from aitrader.domain import (
    Currency,
    Fill,
    FxConversion,
    FxDirection,
    Money,
    Side,
    TradeCost,
    TradeCostType,
)
from aitrader.portfolio import (
    Account,
    CashAccountError,
    DuplicateEntryError,
    EntryKind,
    JournalEntry,
    Ledger,
    LedgerError,
    PositionChange,
    Posting,
)

D = Decimal
USD = Currency.USD
JPY = Currency.JPY
T0 = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
NEXT_DAY = date(2026, 10, 23)


def usd(value: str | int) -> Money:
    return Money.of(value, USD)


def jpy(value: str | int) -> Money:
    return Money.of(value, JPY)


def fill(fill_id: str, side: Side, quantity: str, price: str, **overrides: Any) -> Fill:
    fields: dict[str, Any] = {
        "fill_id": fill_id,
        "client_order_id": f"order-{fill_id}",
        "symbol": "SPY",
        "side": side,
        "quantity": quantity,
        "price": price,
        "ts": T0,
        "source": "sim",
    }
    fields.update(overrides)
    return Fill.model_validate(fields)


def cost(kind: TradeCostType, amount: str, **overrides: Any) -> TradeCost:
    fields: dict[str, Any] = {
        "ts": T0,
        "type": kind,
        "amount": usd(amount),
        "estimated": True,
        "source": "webull_jp model",
    }
    fields.update(overrides)
    return TradeCost.model_validate(fields)


def conversion(direction: FxDirection, jpy_amount: str, usd_amount: str, **kw: Any) -> FxConversion:
    fields: dict[str, Any] = {
        "ts": T0,
        "direction": direction,
        "jpy_amount": jpy(jpy_amount),
        "usd_amount": usd(usd_amount),
        "applied_rate": "155.15",
        "reference_rate": "155.00",
        "source": "statement 2026-10",
    }
    fields.update(kw)
    return FxConversion.model_validate(fields)


def funded(usd_cash: str = "1000") -> Ledger:
    ledger = Ledger()
    ledger.deposit("dep-usd", T0, usd(usd_cash))
    return ledger


# ---------------------------------------------------------------- deposits and withdrawals


def test_jpy_deposit_and_withdrawal_with_bank_fee() -> None:
    ledger = Ledger()
    ledger.deposit("dep-1", T0, jpy(50_000), ref="bank transfer")
    ledger.withdraw("wd-1", T0, jpy(10_000), bank_fee=jpy(330))
    assert ledger.cash_jpy == D(39_670)
    assert ledger.balance(Account.BANK_FEES, JPY) == D(330)
    assert ledger.balance(Account.OWNER_CAPITAL, JPY) == D(-40_000)
    assert all(v == 0 for v in ledger.trial_balance().values())


def test_withdrawal_without_fee() -> None:
    ledger = Ledger()
    ledger.deposit("dep-1", T0, jpy(1000))
    entry = ledger.withdraw("wd-1", T0, jpy(1000))
    assert len(entry.postings) == 2
    assert ledger.cash_jpy == 0


def test_usd_deposit_is_settled_cash() -> None:
    assert funded("323").cash_usd_settled == D(323)


@pytest.mark.parametrize("amount", [jpy(0), jpy(-1)])
def test_deposit_must_be_positive(amount: Money) -> None:
    with pytest.raises(ValueError, match="positive"):
        Ledger().deposit("dep", T0, amount)


@pytest.mark.parametrize(
    ("amount", "fee"),
    [(usd(1), None), (jpy(0), None), (jpy(10), usd(1)), (jpy(10), jpy(-1))],
)
def test_withdrawal_rules(amount: Money, fee: Money | None) -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(100))
    with pytest.raises(ValueError, match="JPY"):
        ledger.withdraw("wd", T0, amount, bank_fee=fee)


def test_cannot_withdraw_more_than_the_cash() -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(1000))
    with pytest.raises(CashAccountError, match="CASH_JPY"):
        ledger.withdraw("wd", T0, jpy(800), bank_fee=jpy(330))
    assert ledger.cash_jpy == D(1000)  # unchanged
    assert len(ledger.entries) == 1


# ---------------------------------------------------------------- FX conversions


def test_jpy_to_usd_books_the_spread_as_fx_cost() -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(50_000))
    ledger.convert_fx("fx-1", conversion(FxDirection.JPY_TO_USD, "50000", "322.26"))
    assert ledger.cash_jpy == 0
    assert ledger.cash_usd_settled == D("322.26")
    # 50,000 - 322.26 * 155.00 = 49.70 JPY against the reference rate
    assert ledger.balance(Account.FX_COST, JPY) == D("49.70")
    assert ledger.entries[-1].ref == "statement 2026-10"


def test_usd_to_jpy_books_the_spread_as_fx_cost() -> None:
    ledger = funded("100")
    ledger.convert_fx(
        "fx-1",
        conversion(
            FxDirection.USD_TO_JPY,
            "15485",
            "100",
            applied_rate="154.85",
            reference_rate="155.00",
        ),
        ref="manual entry",
    )
    assert ledger.cash_usd_settled == 0
    assert ledger.cash_jpy == D(15_485)
    assert ledger.balance(Account.FX_COST, JPY) == D(15)
    assert ledger.entries[-1].ref == "manual entry"


def test_conversion_at_the_reference_rate_has_no_fx_cost() -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(15_500))
    entry = ledger.convert_fx(
        "fx-1", conversion(FxDirection.JPY_TO_USD, "15500", "100", applied_rate="155.00")
    )
    assert all(p.account is not Account.FX_COST for p in entry.postings)


def test_cannot_convert_more_jpy_than_held() -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(10_000))
    with pytest.raises(CashAccountError):
        ledger.convert_fx("fx-1", conversion(FxDirection.JPY_TO_USD, "50000", "322.26"))


# ---------------------------------------------------------------- fills


def test_buy_uses_settled_usd_and_records_each_fee() -> None:
    ledger = funded("100")
    entry = ledger.record_fill(
        fill("b1", Side.BUY, "0.34643", "100.01"),
        [cost(TradeCostType.CAT_FEE, "0.00000104", client_order_id="order-b1")],
    )
    assert entry.kind is EntryKind.BUY
    assert entry.ref == "order-b1"
    position = ledger.position("SPY")
    assert position is not None
    assert position.quantity == D("0.34643")
    assert position.cost_usd == D("34.6464643")
    assert position.average_cost == D("100.01")
    assert ledger.cash_usd_settled == D(100) - D("34.6464643") - D("0.00000104")
    assert ledger.balance(Account.TRADING_FEES, USD, "CAT_FEE") == D("0.00000104")


def test_zero_cost_items_are_not_posted() -> None:
    ledger = funded("100")
    entry = ledger.record_fill(
        fill("b1", Side.BUY, "1", "10"), [cost(TradeCostType.COMMISSION, "0")]
    )
    assert all(p.account is not Account.TRADING_FEES for p in entry.postings)


def test_buy_cannot_use_unsettled_proceeds() -> None:
    ledger = funded("100")
    ledger.record_fill(fill("b1", Side.BUY, "1", "100"))
    ledger.record_fill(fill("s1", Side.SELL, "1", "100"), settles_on=NEXT_DAY)
    assert ledger.cash_usd_settled == 0
    assert ledger.cash_usd_unsettled == D(100)
    with pytest.raises(CashAccountError, match="CASH_USD_SETTLED"):
        ledger.record_fill(fill("b2", Side.BUY, "0.5", "100"))
    ledger.settle(NEXT_DAY, T0 + timedelta(days=1))
    ledger.record_fill(fill("b2", Side.BUY, "0.5", "100", ts=T0 + timedelta(days=1)))
    assert ledger.cash_usd_settled == D(50)


def test_partial_sale_uses_moving_average_cost() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "1", "100"))
    ledger.record_fill(fill("b2", Side.BUY, "2", "130"))  # average 120
    ledger.record_fill(fill("s1", Side.SELL, "1.5", "126"), settles_on=NEXT_DAY)
    position = ledger.position("SPY")
    assert position is not None
    assert position.quantity == D("1.5")
    assert position.cost_usd == D(180)
    # proceeds 189, cost removed 180, gain 9
    assert ledger.balance(Account.REALIZED_PNL, USD, "SPY") == D(-9)


def test_partial_sale_cost_is_rounded_down_and_exact() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "3", "100"))
    ledger.record_fill(fill("s1", Side.SELL, "1", "100"), settles_on=NEXT_DAY)
    position = ledger.position("SPY")
    assert position is not None
    assert position.cost_usd == D("200.0000000000")
    ledger.record_fill(fill("b2", Side.BUY, "0.1", "100"))
    ledger.record_fill(fill("s2", Side.SELL, "0.7", "77.77"), settles_on=NEXT_DAY)
    assert all(v == 0 for v in ledger.trial_balance().values())


def test_sale_of_the_whole_position_removes_all_cost() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "3", "100"))
    ledger.record_fill(fill("s1", Side.SELL, "3", "90"), settles_on=NEXT_DAY)
    assert ledger.position("SPY") is None
    assert ledger.balance(Account.SECURITIES, USD) == 0
    assert ledger.balance(Account.REALIZED_PNL, USD) == D(30)  # a loss is a debit


def test_sale_at_cost_has_no_realised_line() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "1", "100"))
    entry = ledger.record_fill(fill("s1", Side.SELL, "1", "100"), settles_on=NEXT_DAY)
    assert all(p.account is not Account.REALIZED_PNL for p in entry.postings)


def test_sale_fees_come_out_of_the_proceeds() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "10", "100"))
    ledger.record_fill(
        fill("s1", Side.SELL, "10", "101"),
        [cost(TradeCostType.FINRA_FEE, "0.01"), cost(TradeCostType.SEC_FEE, "0.03")],
        settles_on=NEXT_DAY,
    )
    assert ledger.cash_usd_unsettled == D("1009.96")
    assert ledger.pending_settlements[0].amount_usd == D("1009.96")


def test_fees_above_tiny_proceeds_come_from_settled_cash() -> None:
    ledger = funded("10")
    ledger.record_fill(fill("b1", Side.BUY, "0.00001", "100"))
    ledger.record_fill(
        fill("s1", Side.SELL, "0.00001", "100"),
        [cost(TradeCostType.FINRA_FEE, "0.01"), cost(TradeCostType.SEC_FEE, "0.01")],
        settles_on=NEXT_DAY,
    )
    assert ledger.cash_usd_unsettled == 0
    assert ledger.pending_settlements == ()
    assert ledger.cash_usd_settled == D(10) - D("0.001") - D("0.02") + D("0.001")


@pytest.mark.parametrize(("held", "sold"), [(None, "1"), ("1", "1.00001")])
def test_no_short_sales(held: str | None, sold: str) -> None:
    ledger = funded("1000")
    if held is not None:
        ledger.record_fill(fill("b1", Side.BUY, held, "100"))
    with pytest.raises(CashAccountError, match="no short sales"):
        ledger.record_fill(fill("s1", Side.SELL, sold, "100"), settles_on=NEXT_DAY)


def test_sale_needs_a_settlement_date() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "1", "100"))
    with pytest.raises(ValueError, match="settlement date"):
        ledger.record_fill(fill("s1", Side.SELL, "1", "100"))


def test_buy_takes_no_settlement_date() -> None:
    with pytest.raises(ValueError, match="sales only"):
        funded().record_fill(fill("b1", Side.BUY, "1", "100"), settles_on=NEXT_DAY)


def test_fill_costs_must_be_usd_and_belong_to_the_order() -> None:
    ledger = funded()
    jpy_cost = cost(TradeCostType.OTHER, "1").model_copy(update={"amount": jpy(1)})
    with pytest.raises(ValueError, match="USD"):
        ledger.record_fill(fill("b1", Side.BUY, "1", "100"), [jpy_cost])
    other = cost(TradeCostType.CAT_FEE, "0.000003", client_order_id="another-order")
    with pytest.raises(ValueError, match="different order"):
        ledger.record_fill(fill("b1", Side.BUY, "1", "100"), [other])


# ---------------------------------------------------------------- idempotency and ordering


def test_the_same_fill_twice_is_recorded_once() -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "2", "100"))
    sale = fill("s1", Side.SELL, "1", "100")
    fees = [cost(TradeCostType.FINRA_FEE, "0.01")]
    first = ledger.record_fill(sale, fees, settles_on=NEXT_DAY)
    again = ledger.record_fill(sale, fees, settles_on=NEXT_DAY)
    assert again is first
    assert len(ledger.entries) == 3
    position = ledger.position("SPY")
    assert position is not None
    assert position.quantity == 1


@pytest.mark.parametrize(
    "change",
    [
        {"fill": fill("s1", Side.SELL, "0.5", "100")},
        {"fees": [cost(TradeCostType.FINRA_FEE, "0.02")]},
        {"settles_on": date(2026, 10, 26)},
    ],
)
def test_a_different_fill_under_a_used_id_is_refused(change: dict[str, Any]) -> None:
    ledger = funded("1000")
    ledger.record_fill(fill("b1", Side.BUY, "2", "100"))
    ledger.record_fill(
        fill("s1", Side.SELL, "1", "100"),
        [cost(TradeCostType.FINRA_FEE, "0.01")],
        settles_on=NEXT_DAY,
    )
    args: dict[str, Any] = {
        "fill": fill("s1", Side.SELL, "1", "100"),
        "fees": [cost(TradeCostType.FINRA_FEE, "0.01")],
        "settles_on": NEXT_DAY,
    }
    args.update(change)
    with pytest.raises(DuplicateEntryError):
        ledger.record_fill(args["fill"], args["fees"], settles_on=args["settles_on"])


def test_the_same_entry_twice_is_a_no_op() -> None:
    ledger = Ledger()
    first = ledger.deposit("dep", T0, jpy(100))
    assert ledger.deposit("dep", T0, jpy(100)) is first
    assert ledger.cash_jpy == 100
    with pytest.raises(DuplicateEntryError):
        ledger.deposit("dep", T0, jpy(200))


def test_entries_must_be_in_time_order() -> None:
    ledger = Ledger()
    ledger.deposit("dep-1", T0, jpy(100))
    with pytest.raises(LedgerError, match="time order"):
        ledger.deposit("dep-2", T0 - timedelta(seconds=1), jpy(100))


# ---------------------------------------------------------------- settlement


def test_settlement_happens_on_the_settlement_date() -> None:
    ledger = funded("200")
    ledger.record_fill(fill("b1", Side.BUY, "2", "100"))
    ledger.record_fill(fill("s1", Side.SELL, "1", "100"), settles_on=NEXT_DAY)
    ledger.record_fill(fill("s2", Side.SELL, "1", "100"), settles_on=date(2026, 10, 26))
    assert ledger.settle(date(2026, 10, 22), T0) == []
    settled = ledger.settle(NEXT_DAY, T0 + timedelta(days=1))
    assert [e.ref for e in settled] == ["fill:s1"]
    assert ledger.cash_usd_settled == D(100)
    assert ledger.cash_usd_unsettled == D(100)
    assert ledger.settle(NEXT_DAY, T0 + timedelta(days=1)) == []  # nothing settles twice
    ledger.settle(date(2026, 10, 30), T0 + timedelta(days=5))
    assert ledger.cash_usd_unsettled == 0
    assert ledger.pending_settlements == ()


def _settlement(ref: str, amount: str) -> JournalEntry:
    return JournalEntry(
        entry_id=f"settle:{ref}",
        ts=T0,
        kind=EntryKind.SETTLEMENT,
        ref=ref,
        postings=(
            Posting(account=Account.CASH_USD_SETTLED, amount=usd(amount)),
            Posting(account=Account.CASH_USD_UNSETTLED, amount=usd(f"-{amount}")),
        ),
    )


def test_settlement_must_match_a_pending_sale() -> None:
    ledger = funded("200")
    ledger.record_fill(fill("b1", Side.BUY, "1", "100"))
    ledger.record_fill(fill("s1", Side.SELL, "1", "100"), settles_on=NEXT_DAY)
    with pytest.raises(LedgerError, match="no pending settlement"):
        ledger.apply(_settlement("fill:unknown", "100"))
    with pytest.raises(LedgerError, match="exactly the pending amount"):
        ledger.apply(_settlement("fill:s1", "50"))


def test_only_sales_and_settlements_touch_unsettled_cash() -> None:
    ledger = funded("100")
    entry = JournalEntry(
        entry_id="odd",
        ts=T0,
        kind=EntryKind.COST,
        postings=(
            Posting(account=Account.CASH_USD_UNSETTLED, amount=usd(1)),
            Posting(account=Account.CASH_USD_SETTLED, amount=usd(-1)),
        ),
    )
    with pytest.raises(LedgerError, match="only sales and settlements"):
        ledger.apply(entry)


def test_a_sale_cannot_reduce_unsettled_cash() -> None:
    ledger = funded("100")
    ledger.record_fill(fill("b1", Side.BUY, "1", "10"))
    sale = fill("s1", Side.SELL, "1", "10")
    entry = JournalEntry(
        entry_id="fill:s1",
        ts=T0,
        kind=EntryKind.SELL,
        postings=(
            Posting(account=Account.SECURITIES, amount=usd(-10), detail="SPY"),
            Posting(account=Account.CASH_USD_UNSETTLED, amount=usd(-1)),
            Posting(account=Account.CASH_USD_SETTLED, amount=usd(11)),
        ),
        position_change=PositionChange(symbol="SPY", quantity=D(-1), cost_usd=D(-10)),
        settles_on=NEXT_DAY,
        fill=sale,
    )
    with pytest.raises(LedgerError, match="cannot reduce unsettled"):
        ledger.apply(entry)


# ---------------------------------------------------------------- dividends and other costs


def test_dividend_with_us_and_japanese_withholding() -> None:
    ledger = funded("0.01")
    ledger.record_dividend(
        "div-1",
        T0,
        "SPY",
        gross=usd("1.00"),
        us_withholding=usd("0.10"),
        jp_withholding=usd("0.18"),
        ref="SPY 2026-Q3",
    )
    assert ledger.cash_usd_settled == D("0.73")
    assert ledger.balance(Account.DIVIDEND_INCOME, USD, "SPY") == D("-1.00")
    assert ledger.balance(Account.WITHHOLDING_TAX, USD, "US") == D("0.10")
    assert ledger.balance(Account.WITHHOLDING_TAX, USD, "JP") == D("0.18")


def test_dividend_without_withholding() -> None:
    ledger = Ledger()
    entry = ledger.record_dividend("div-1", T0, "SPY", gross=usd("0.5"))
    assert len(entry.postings) == 2
    assert ledger.cash_usd_settled == D("0.5")


def test_dividend_fully_withheld_credits_no_cash() -> None:
    ledger = Ledger()
    entry = ledger.record_dividend("div-1", T0, "SPY", gross=usd(1), us_withholding=usd(1))
    assert all(p.account is not Account.CASH_USD_SETTLED for p in entry.postings)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"gross": usd(0)},
        {"gross": jpy(1)},
        {"gross": usd(1), "us_withholding": jpy(1)},
        {"gross": usd(1), "jp_withholding": usd(-1)},
        {"gross": usd(1), "us_withholding": usd("0.6"), "jp_withholding": usd("0.6")},
    ],
)
def test_invalid_dividends(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"dividend|withholding"):
        Ledger().record_dividend("div", T0, "SPY", **kwargs)


def test_other_trading_cost_is_paid_from_settled_usd() -> None:
    ledger = funded("10")
    ledger.record_cost("adr-1", cost(TradeCostType.ADR_FEE, "0.02", client_order_id="c1"))
    ledger.record_cost("adr-2", cost(TradeCostType.ADR_FEE, "0.03"))
    assert ledger.cash_usd_settled == D("9.95")
    assert ledger.balance(Account.TRADING_FEES, USD, "ADR_FEE") == D("0.05")
    assert [e.ref for e in ledger.entries[-2:]] == ["c1", "webull_jp model"]


@pytest.mark.parametrize("amount", [usd(0), jpy(1)])
def test_other_trading_cost_must_be_positive_usd(amount: Money) -> None:
    item = cost(TradeCostType.OTHER, "1").model_copy(update={"amount": amount})
    with pytest.raises(ValueError, match="positive USD"):
        funded().record_cost("c", item)


# ---------------------------------------------------------------- valuation and P&L


def _scenario() -> Ledger:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(50_000))
    ledger.convert_fx("fx", conversion(FxDirection.JPY_TO_USD, "50000", "322.26"))
    ledger.record_fill(fill("b1", Side.BUY, "1", "100"), [cost(TradeCostType.CAT_FEE, "0.01")])
    ledger.record_fill(
        fill("s1", Side.SELL, "0.5", "110"),
        [cost(TradeCostType.FINRA_FEE, "0.01"), cost(TradeCostType.SEC_FEE, "0.01")],
        settles_on=NEXT_DAY,
    )
    ledger.record_dividend("div", T0, "SPY", gross=usd("0.20"), us_withholding=usd("0.02"))
    return ledger


def test_valuation() -> None:
    valuation = _scenario().valuation({"SPY": D(104)}, D(150))
    assert valuation.market_value_usd == D(52)
    assert valuation.cost_basis_usd == D(50)
    assert valuation.unrealized_pnl_usd == D(2)
    assert valuation.cash_usd_unsettled == D("54.98")
    assert valuation.equity_usd == valuation.cash_usd_settled + D("54.98") + D(52)
    assert valuation.equity_jpy == valuation.equity_usd * 150


def test_trading_pnl_breakdown_reconciles_to_equity() -> None:
    ledger = _scenario()
    pnl = ledger.trading_pnl({"SPY": D(104)}, D(150))
    assert pnl.realized_usd == D(5)
    assert pnl.unrealized_usd == D(2)
    assert pnl.dividends_usd == D("0.20")
    assert pnl.withholding_usd == D("0.02")
    assert pnl.fees_by_type_usd == {
        "CAT_FEE": D("0.01"),
        "FINRA_FEE": D("0.01"),
        "SEC_FEE": D("0.01"),
    }
    assert pnl.fees_usd == D("0.03")
    assert pnl.trading_pnl_usd == D("7.15")
    assert pnl.fx_cost_jpy == D("49.70")
    assert pnl.fx_translation_jpy == D("322.26") * 150 - D("322.26") * 155
    assert pnl.capital_jpy == D(50_000)
    valuation = ledger.valuation({"SPY": D(104)}, D(150))
    assert valuation.equity_jpy == pnl.capital_jpy_equivalent + pnl.trading_pnl_jpy


def test_valuation_needs_a_price_for_every_position() -> None:
    with pytest.raises(LedgerError, match="no price for SPY"):
        _scenario().valuation({}, D(150))


def test_valuation_needs_a_positive_fx_rate() -> None:
    with pytest.raises(ValueError, match="FX rate"):
        _scenario().valuation({"SPY": D(1)}, D(0))


def test_ledger_can_be_rebuilt_and_viewed_at_a_time() -> None:
    ledger = _scenario()
    later = T0 + timedelta(days=1)
    ledger.settle(NEXT_DAY, later)
    rebuilt = Ledger.from_entries(ledger.entries)
    assert rebuilt == ledger
    before = ledger.as_of(T0)
    assert before.cash_usd_unsettled == D("54.98")
    assert ledger.cash_usd_unsettled == 0
    assert ledger.entry("fx") is not None
    assert ledger.entry("missing") is None


def test_balances_omit_zero_lines() -> None:
    ledger = Ledger()
    ledger.deposit("dep", T0, jpy(100))
    ledger.withdraw("wd", T0, jpy(100))
    assert ledger.balances() == {}


# ---------------------------------------------------------------- journal entry validation


def _entry(**overrides: Any) -> JournalEntry:
    fields: dict[str, Any] = {
        "entry_id": "e1",
        "ts": T0,
        "kind": EntryKind.DEPOSIT,
        "postings": (
            Posting(account=Account.CASH_JPY, amount=jpy(1)),
            Posting(account=Account.OWNER_CAPITAL, amount=jpy(-1)),
        ),
    }
    fields.update(overrides)
    return JournalEntry.model_validate(fields)


def test_unbalanced_entry_is_refused() -> None:
    postings = (
        Posting(account=Account.CASH_JPY, amount=jpy(1)),
        Posting(account=Account.OWNER_CAPITAL, amount=jpy(-2)),
    )
    with pytest.raises(ValidationError, match="does not balance"):
        _entry(postings=postings)


def test_each_currency_must_balance_on_its_own() -> None:
    postings = (
        Posting(account=Account.CASH_JPY, amount=jpy(155)),
        Posting(account=Account.OWNER_CAPITAL, amount=usd(-1)),
    )
    with pytest.raises(ValidationError, match="does not balance"):
        _entry(postings=postings)


@pytest.mark.parametrize(
    ("account", "amount"),
    [
        (Account.CASH_JPY, usd(1)),
        (Account.CASH_USD_SETTLED, jpy(1)),
        (Account.FX_COST, usd(1)),
        (Account.TRADING_FEES, jpy(1)),
    ],
)
def test_accounts_keep_their_currency(account: Account, amount: Money) -> None:
    with pytest.raises(ValidationError, match="is kept in"):
        Posting(account=account, amount=amount)


def test_zero_postings_are_refused() -> None:
    with pytest.raises(ValidationError, match="must not be zero"):
        Posting(account=Account.CASH_JPY, amount=jpy(0))


def _trade_postings(symbol: str = "SPY") -> tuple[Posting, ...]:
    return (
        Posting(account=Account.SECURITIES, amount=usd(10), detail=symbol),
        Posting(account=Account.CASH_USD_SETTLED, amount=usd(-10)),
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": EntryKind.BUY}, "carry a fill"),
        ({"postings": _trade_postings()}, "only BUY and SELL entries post to SECURITIES"),
        ({"kind": EntryKind.SETTLEMENT}, "reference the SELL entry"),
        ({"settles_on": NEXT_DAY}, "carry a settlement date"),
    ],
)
def test_entry_kind_rules(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _entry(**overrides)


@pytest.mark.parametrize(
    ("change", "the_fill", "postings", "message"),
    [
        (
            PositionChange(symbol="SPY", quantity=D(1), cost_usd=D(10)),
            fill("x", Side.SELL, "1", "10"),
            _trade_postings(),
            "does not match the entry kind",
        ),
        (
            PositionChange(symbol="SPY", quantity=D(1), cost_usd=D(10)),
            fill("x", Side.BUY, "1", "10", symbol="QQQ"),
            _trade_postings(),
            "does not match the entry kind",
        ),
        (
            PositionChange(symbol="SPY", quantity=D(2), cost_usd=D(10)),
            fill("x", Side.BUY, "1", "10"),
            _trade_postings(),
            "equal the filled quantity",
        ),
        (
            PositionChange(symbol="SPY", quantity=D(1), cost_usd=D(10)),
            fill("x", Side.BUY, "1", "10"),
            _trade_postings("QQQ"),
            "at most one position",
        ),
        (
            PositionChange(symbol="SPY", quantity=D(1), cost_usd=D(9)),
            fill("x", Side.BUY, "1", "10"),
            _trade_postings(),
            "equal the position's cost change",
        ),
    ],
)
def test_trade_entry_rules(
    change: PositionChange, the_fill: Fill, postings: tuple[Posting, ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _entry(kind=EntryKind.BUY, position_change=change, fill=the_fill, postings=postings)


def test_inconsistent_cost_basis_is_refused() -> None:
    ledger = funded("100")
    entry = JournalEntry(
        entry_id="fill:x",
        ts=T0,
        kind=EntryKind.BUY,
        postings=(
            Posting(account=Account.SECURITIES, amount=usd(-1), detail="SPY"),
            Posting(account=Account.CASH_USD_SETTLED, amount=usd(1)),
        ),
        position_change=PositionChange(symbol="SPY", quantity=D(1), cost_usd=D(-1)),
        fill=fill("x", Side.BUY, "1", "10"),
    )
    with pytest.raises(LedgerError, match="inconsistent cost basis"):
        ledger.apply(entry)


def test_short_sale_through_a_raw_entry_is_refused() -> None:
    ledger = funded("100")
    ledger.record_fill(fill("b1", Side.BUY, "1", "10"))
    entry = JournalEntry(
        entry_id="fill:s1",
        ts=T0,
        kind=EntryKind.SELL,
        postings=(
            Posting(account=Account.SECURITIES, amount=usd(-10), detail="SPY"),
            Posting(account=Account.CASH_USD_UNSETTLED, amount=usd(20)),
            Posting(account=Account.REALIZED_PNL, amount=usd(-10), detail="SPY"),
        ),
        position_change=PositionChange(symbol="SPY", quantity=D(-2), cost_usd=D(-10)),
        settles_on=NEXT_DAY,
        fill=fill("s1", Side.SELL, "2", "10"),
    )
    with pytest.raises(CashAccountError, match="no short sales"):
        ledger.apply(entry)


def test_sale_whose_fees_equal_the_proceeds_leaves_nothing_to_settle() -> None:
    ledger = funded("10")
    ledger.record_fill(fill("b1", Side.BUY, "1", "0.02"))
    entry = ledger.record_fill(
        fill("s1", Side.SELL, "1", "0.02"),
        [cost(TradeCostType.FINRA_FEE, "0.01"), cost(TradeCostType.SEC_FEE, "0.01")],
        settles_on=NEXT_DAY,
    )
    cash_accounts = {Account.CASH_USD_SETTLED, Account.CASH_USD_UNSETTLED}
    assert all(p.account not in cash_accounts for p in entry.postings)
    assert ledger.pending_settlements == ()
