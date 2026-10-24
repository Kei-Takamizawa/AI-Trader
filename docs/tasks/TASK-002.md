# TASK-002: Risk engine with fee model, sizing and pre-trade rules

Implementer: Claude (owner instruction 2026-10-23). Design sources: documents 05 (rules and
sizing), 03 (package rules), 14 (Webull fees and fractional rules).

Status: **done** (2026-10-23). 384 tests, 100% line and branch coverage of `fees` and `risk`;
ruff, mypy (strict) and lint-imports (5 contracts, including the new `fees` contract) green.

## Scope

1. **`aitrader.fees`** (new, pure, depends only on `aitrader.domain`): `FeeSchedule` and
   `ExecutionAssumptions`; the `WEBULL_JP` schedule from the official pricing page
   (commission 0; CAT USD 0.000003/share both sides; FINRA USD 0.000195/share sold, min 0.01,
   max 9.79, whole and fractional parts charged separately; SEC USD 0.0000206 per USD sold,
   rounded up to the cent; FX JPY 0.15 per USD per conversion; fractional buys ≥ USD 1).
   Functions for per-order fees (as typed items) and round-trip cost estimates, with a
   conservative (monotonic) variant for sizing.
2. **`aitrader.domain`**: `IntentPurpose` (ENTRY, EXIT, STOP_EXIT, FLATTEN) on `OrderIntent`.
3. **`aitrader.risk`**: input snapshots (`RiskState`, `PositionSnapshot`, `OpenOrderSnapshot`,
   `SymbolMarketState`, `MarketWideState`), `RiskParameters` (thresholds from document 05 that
   are not config limits yet), sizing (document 05 §2, fees included, conservative cost bound,
   binary search on quantity increments) and `RiskEngine.evaluate()` returning a `RiskDecision`
   with the result of **every** rule (no short-circuit).
4. Rules for entries: STATE-1, R-INST-1, R-EVT-1, DQ-1, DQ-2..DQ-5 (flags from the data gate),
   DQ-6, CB-1..CB-5, R-DAY-1, R-MONTH-1 (halves risk), R-MONTH-2, R-DD-1, R-ORD-1, R-POS-1,
   DUP-1, DUP-3, DUP-4, sizing caps R-TRADE-1, R-POS-2, R-PORT-1, R-PORT-2, R-PORT-3 (bound with
   correlation 1), R-ORD-2, R-LIVE-1..3, R-ECON-1..3, and the AI overlay AI-1 (veto or
   multiplier ≤ 1 from valid advisories only).
5. Rules for exits (SELL): state (SAFE blocks; KILLED allows only STOP_EXIT and FLATTEN), the
   position must exist, quantity ≤ available (never short), DUP-1, DUP-3. Exits ignore data,
   circuit-breaker, loss-limit and AI rules (they reduce risk).

## Out of scope

Order manager, order gate and halt state machine (TASK-004), ledger (TASK-003), data quality
gate implementation (TASK-006; its results arrive here as flags).

## Acceptance criteria

- Every rule ID has tests at, just below and just above its threshold where it has one.
- Property tests (hypothesis): no approved entry exceeds the risk budget including fees, the
  position, exposure, cash, live-order and live-holdings caps; quantities are multiples of the
  increment; AI never increases a quantity; a SELL never exceeds the available quantity.
- Worked example from document 05 §2 reproduced (¥50,000, stop 4% below entry).
- ruff, mypy (strict), lint-imports and pytest green; coverage ≥ 90% for `fees` and `risk`.

## Implementation notes

- Rounding to the quantity increment alone is not a reduction: the verdict is REDUCE only when a
  cap, the risk budget, the available quantity (exits) or the AI multiplier lowered the quantity.
- Exits never use more than `available_quantity` (quantity not reserved by other open orders).
  Without market data, partial exits round to the fractional step.
- Earnings dates are compared with the UTC date of `now`, which equals the New York date during
  regular trading hours (the only session allowed).
- `RiskParameters` hold thresholds that are not configuration limits yet; they are part of
  `limits_hash`, so any change is visible in the decision records.
