# TASK-003: Ledger (cash by currency, settlement, fees, FX, dividends, operating costs)

Implementer: Claude (owner instruction 2026-10-23). Design sources: documents 03 (§2 `portfolio`,
§6 `cash_ledger`, `trade_costs`, `fx_conversions`, `operating_costs`), 08 (§3a, §4 cost
recording), 12 (settlement), 14 (Webull fees, FX and statements).

Status: **done** (2026-10-23). 100% line and branch coverage of `portfolio`; ruff, mypy (strict)
and lint-imports (6 contracts) green.

## Scope

1. **Domain:** `Fill` (one execution) and `OperatingCost` with `OperatingCostCategory`
   (AI, SERVER, DATA, OTHER).
2. **`aitrader.portfolio`** (pure; depends only on `aitrader.domain`):
   - Chart of accounts and `JournalEntry` / `Posting`. Every entry balances **per currency**
     (debits positive, credits negative); accounts have fixed currencies where it matters.
   - `Ledger`, built only by applying journal entries, so it can be rebuilt from its entries
     (`Ledger.from_entries`, `Ledger.as_of`). Entry IDs make recording idempotent: the same
     entry again is a no-op, a different entry under a used ID is an error.
   - Recording: deposits and withdrawals (with bank fee), JPY⇄USD conversions (statement amounts,
     spread against the reference rate booked as FX cost), fills with their trading costs, T+1
     settlement of sale proceeds, dividends with US and Japanese withholding, other trading costs.
   - Positions at **moving-average cost** (the method used for Japanese tokutei accounts);
     realised P&L per sale is gross of fees, and every fee is its own expense line by type.
   - Cash-account rules: buys use **settled USD only**; settled USD, unsettled USD and JPY cash
     never go negative; no short positions. A violation raises and leaves the ledger unchanged.
   - Valuation (cash, market value, unrealised P&L, equity in USD and JPY) and a trading P&L
     breakdown (realised, unrealised, dividends, withholding, fees by type, FX cost, bank fees,
     FX translation) that reconciles exactly to equity.
3. **Operating costs** in a separate `OperatingCostBook` (never posted to the trading ledger, since
   they are paid outside the brokerage account) and a report with three lines: trading P&L,
   operating costs, net (document 08 §4).

## Out of scope

Persistence (storage task), replacing estimated fees with broker-reported ones (TASK-008),
capital-gains withholding in the tokutei account (booked from statements once LIVE exists),
corporate actions (splits), the exchange calendar (callers pass the settlement date).

## Acceptance criteria

- Unit tests for every recording function, including the error cases (insufficient settled cash,
  short sale, unbalanced entry, wrong currency, duplicate ID, out-of-order timestamps).
- Property tests (hypothesis, stateful): after any sequence of operations the trial balance is
  zero in each currency; cash is never negative; position cost equals the securities account;
  equity equals capital plus trading P&L; replaying the entries gives the same ledger.
- ruff, mypy (strict), lint-imports (new contract: `portfolio` depends only on `domain`) and
  pytest green; coverage ≥ 90% for `portfolio`.

## Implementation notes

- Sign convention: debits positive, credits negative. Equity in JPY equals net deposits plus
  trading P&L, where trading P&L = (realised + unrealised + dividends − withholding − fees) × FX
  − FX cost − bank fees + FX translation. The stateful property test checks this identity exactly.
- Cost removed by a partial sale is rounded down to 1e-10 USD, so every sum stays exact in the
  default 28-digit decimal context; selling a whole position removes all of its cost.
- Withholding taxes are booked in USD. Whether Webull withholds the Japanese tax on US dividends
  in USD or JPY is not verified yet (document 14, WB-8); the API accepts USD only for now.
- Operating costs in USD are converted at the report's FX rate.
