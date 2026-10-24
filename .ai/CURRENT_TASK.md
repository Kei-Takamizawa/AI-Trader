# CURRENT_TASK — TASK-014: Strategy S3, neighbour stability, cost share, faster backtests

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-013.md`.

Design sources:
- `docs/research/2026-10-24-s3-plan.md`: the declared S3 plan. **Read it first.** This task
  implements its §2 and §5 and nothing else. The owner approved the plan and the S3-R1 research
  runs on 2026-10-24.
- `src/aitrader/strategy/momentum.py` (S1: the closest existing strategy; reuse its patterns),
  `src/aitrader/backtest/walkforward.py`, `src/aitrader/backtest/engine.py`,
  `src/aitrader/metrics/performance.py`, `src/aitrader/app/paper.py`.

No protected path is touched: `risk/`, `hard_caps.py`, `execution/gate*`, `broker/webull/`,
`config/*.yaml` stay unchanged.

## Scope

### A. Strategy S3 1.0.0 (`aitrader.strategy.trend_core`)

- Imports only `aitrader.domain` and `aitrader.indicators` (import-linter contract).
- `TrendCoreParams` (frozen dataclass like `MomentumParams`): `core = ("SPY", "QQQ", "IWM", "GLD")`,
  `defensive = "SHY"`, `trend_period = 200`, `atr_period = 20`, `atr_stop_multiple = Decimal(3)`,
  `target_weight = Decimal("0.25")`. `strategy_id = "S3"`, `version = "1.0.0"`. `params()` like S1.
- Review: only on the last session of the ISO week (same test as S1); on other sessions return
  no intents.
- At a review, for each core symbol: it is **wanted** when it has a bar on the session, at least
  `trend_period` closes and an ATR, and its last close is strictly above SMA(`trend_period`) of
  the closes. Otherwise it is unwanted, with a reason (no bar, not enough history, below the
  trend average).
- Let `u` = number of unwanted core symbols.
- Intents, in this order (deterministic, sorted by symbol within each group):
  1. SELL EXIT the full holding of every held core symbol that is unwanted;
  2. SHY: if held and `u == 0`, SELL EXIT all; if held and its value at the last close exceeds
     `equity × target_weight × u + equity × 0.05`, SELL EXIT the excess quantity rounded down to
     the fractional step `Decimal("0.00001")` (skip if that rounds to zero); if not held and
     `u > 0` and SHY has a bar and an ATR, BUY ENTRY with `target_notional = equity ×
     target_weight × u`; SHY is never subject to the trend filter;
  3. BUY ENTRY every wanted core symbol not held, `target_notional = equity × target_weight`.
- Every BUY carries `stop_price = close − atr_stop_multiple × ATR(atr_period)` quantized to cents;
  skip the BUY if the stop is ≤ 0. Holdings are never topped up.
- Reasons are short and specific ("above the 200-session trend average", "below the trend
  average", "defensive asset for 2 unwanted core slots", "reduce the defensive asset").
- Register `"S3": TrendCoreStrategy` in `STRATEGIES`, so `aitrader backtest --strategy S3` works.

Tests: no intents off-review; all wanted → four buys, no SHY; two unwanted → two core buys and
SHY at 2 slots; an unwanted held core symbol is sold; SHY sold when `u == 0`; SHY reduced when
over target by more than 5% of equity, kept when within; missing bar, short history and missing
ATR are unwanted with the right reason; stop ≤ 0 skipped; SHY never filtered by trend; params
and hash stable.

### B. Walk-forward for S3

- `variants("S3")`: the declared grid `trend_period ∈ (200, 150)` × `atr_stop_multiple ∈ (3, 2)`,
  in that order (it decides exact ties).
- `--cash-proxy` is refused for S3 (usage error, as for S2). `--risk-per-trade-pct` and
  `--prior-variants` work as for S1.
- Represent every grid by its axes (for S1: `top_n (3, 2)`, `skip (0, 5)`), so neighbours can be
  derived in C. S1's grid order and results must not change.

### C. Neighbour stability and cost share in the walk-forward output (all strategies)

1. **Neighbours:** for each fold, after selection, also run the test window for every
   neighbour of the selected grid point: a grid point that differs in exactly one axis by one
   position in that axis's declared order. Each fold report gets `neighbours: [{params,
   params_hash, total_pct}]` and `stable: bool` = every neighbour's test `total_pct` has the same
   sign as the selected point's (positive, zero and negative are three different signs).
   `fold_summary` gets `stable`, and the chained summary gets `stability.stable_folds` and
   `stability.folds`. A grid with one point (S2) has no neighbours; `stable` is `null` and the
   fold is not counted in `folds`.
2. **Costs:** each fold's `test_metrics` includes its `costs` block, `fold_summary` gets
   `cost_share_of_gross_pnl_pct`, and the chained summary gets `costs`: the sum over folds of
   fees, spread and slippage and total in USD, `share_of_gross_pnl_pct` (total costs / sum of fold
   gross P&L, `null` if that sum ≤ 0), and `annual_pct_of_start_equity` (total costs / initial
   capital / years of test sessions × 100).
3. Neighbour runs never influence selection or the chained equity; they are reporting only.

### D. Faster backtests: snapshot capture is opt-in

- TASK-013 made every backtest session build a `SessionSnapshot` (with data-quality findings);
  a real-data walk-forward went from 297 s to 623 s. Add `capture_sessions: bool = False` to
  `run_backtest`; only the PAPER runner passes `True`.
- PAPER behaviour, snapshots and hashes must be unchanged (existing paper tests pass unchanged).
- Report in LAST_REPORT the fixture walk-forward wall time before and after this change.

### E. Docs

- `docs/dev/setup.md`: S3 commands for both families with `--prior-variants 21` (N = 25); S3-R1
  adds `--risk-per-trade-pct 1.0` and is research only (approved by the owner on 2026-10-24).
- Document 11 (strategies): a short S3 1.0.0 section pointing to the plan.

## Out of scope

- Running the real-data research (the operator runs it after the merge).
- Any change to risk limits, hard caps, configuration files, the execution path or S1/S2 logic.
- Using S3 in PAPER (a later decision, only if S3 passes).

## Acceptance criteria

1. All existing tests pass unchanged, except where the walk-forward output gains fields; S1 and
   S2 walk-forward selections, chained equity and metrics are unchanged (compare the fixture
   walk-forward output before and after; only new fields may differ).
2. The fixture backtest double run is byte-identical to the pre-task output (manifest and run ID
   included).
3. Tests for every case in A, B and C, and a test that `run_backtest` without
   `capture_sessions` returns no snapshots while PAPER still records them.
4. A fixture walk-forward `--strategy S3 --risk-per-trade-pct 1.0 --prior-variants 21` runs; if
   the fixtures lack some core symbols or SHY, the missing ones are unwanted (say which in
   LAST_REPORT) and the run still completes.
5. All checks in `AGENTS.md` green; coverage 100% where it is 100% today and never below 90%.
6. No change to protected paths or configuration files (`git diff --stat` in LAST_REPORT).

## Review round 1 (designer, 2026-10-24) — implementation 1a4c33e accepted; one fix

The TASK-014 implementation (1a4c33e) meets the scope above. One finding from the Codex GitHub
review of the merged TASK-013 (PR #9, `src/aitrader/app/paper.py`) must be fixed in this PR,
before the first real PAPER run on 2026-10-26:

**R1. Recover missing daily reports.** Today the daily reports are written only for sessions
inserted in this run, after the database transaction commits. If the process stops or a report
write fails after the commit, the recorded session never gets its report (or gets only one of the
JSON/Markdown pair), because later runs no longer treat it as new.

- After verification, for **every** recorded session up to `through` (the ones just inserted
  and the ones recorded earlier), write each of `reports/daily/YYYY-MM-DD.json` and `.md` that
  is missing. Never rewrite an existing report file. Build the report from the verified replay
  snapshot (identical to the recorded one), with the same previous-equity rule as today.
- A report write failure after the commit must not leave the book in a state that a later run
  cannot repair: the run reports the error (exit 4, status `HALTED` with the reason), and the next
  successful run writes the missing files.
- Tests: (1) delete one session's `.md` and another's `.json` after a successful run; the next
  run recreates exactly those files, leaves the others byte-identical, inserts nothing and exits
  0; (2) simulate a failure while writing reports after the commit (for example a patched writer
  that raises once); the run exits 4 with `HALTED`, and the next run writes the missing reports
  and returns to `RUNNING`.
- Update the `docs/dev/setup.md` PAPER section in one sentence: missing reports are recreated on
  the next run.

Acceptance: the criteria above still hold; all `AGENTS.md` checks green; coverage 100% for the
touched modules.
