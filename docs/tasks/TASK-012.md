# CURRENT_TASK — TASK-012: S1 v1.1 research support (cash proxy, risk override, deflated Sharpe)

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-008.md`.

Design sources:
- `docs/research/2026-10-24-s1-v1.1-plan.md`: the declared plan. **Read it first.** This task
  implements its §4 and nothing else.
- `docs/research/2026-10-24-p2-walkforward.md`: the P2 results and diagnosis;
- document 11 §3 (S1), document 12 §3 (walk-forward) and §6 (metrics, deflated Sharpe);
- `src/aitrader/strategy/momentum.py`, `src/aitrader/backtest/walkforward.py`,
  `src/aitrader/metrics/performance.py`, `src/aitrader/cli/main.py`.

No protected path is touched. `hard_caps.py`, `risk/` and `config/*.yaml` stay unchanged. The
override in B is applied in memory to a research run only.

## Scope

### A. Cash proxy for S1 (plan C1)

- `MomentumParams.cash_proxy: str | None = None`. When it is `None`, behaviour is byte-identical
  to 1.0.0; the existing tests and the fixture backtest prove it.
- When it is set (`"SHY"`):
  - the proxy symbol is removed from the momentum ranking and the equivalents handling;
  - after selection, let `empty = top_n - len(selected)`. If `empty > 0`, the strategy wants one
    position in the proxy with target weight `target_weight × empty`. The risk engine applies
    its normal sizing and caps (the 25% ETF cap will usually bind; that is intended);
  - if `empty == 0`, any proxy position is exited at the next review like any other holding that
    is no longer wanted;
  - the proxy has no trend filter and no momentum requirement. It uses the same ATR stop rule as
    other holdings, so a stop-out is handled the same way;
  - the proxy must have bars on the session; if it has none, it stays in cash (log the reason in
    the decision reasons, as for other symbols).
- `params()` includes `cash_proxy`, so `params_hash` differs from 1.0.0 only when it is set.
- Tests: no qualifying ETF → the proxy at `top_n × target_weight` intent; 1 of 3 qualifying → the
  proxy at 2 slots; all qualifying → the proxy is exited; the proxy is never ranked; missing proxy
  bars → cash; `cash_proxy=None` gives identical intents to today on the existing fixtures.

### B. Research-only risk override and v1.1 families on `aitrader walkforward` (plan C2, §2)

- New option `--risk-per-trade-pct <Decimal>` (default: none → configuration value).
  - It is validated: `0 < value ≤ HARD_CAPS["risk_per_trade_pct"]`; anything else exits with the
    usage-error code and a clear message. Decimal parsing only (no float).
  - It is applied by `model_copy` on the loaded `Settings.risk`, only for this command, and only in
    BACKTEST mode. It never writes a file.
  - The manifest records `risk_override: {"risk_per_trade_pct": "<value>"}`, or `null`.
- New option `--cash-proxy <SYMBOL>` (default none), only for `--strategy S1`. It builds the S1
  grid with that `cash_proxy` on every grid point. The grid points stay as in `S1_GRID`.
- New option `--prior-variants <int>` (default 0). The manifest gets
  `variants_tried_total = prior_variants + len(grid)`, and `variants_tried` (this run's grid)
  stays as today.
- `run_walkforward` gets these as explicit parameters (no globals). Its existing behaviour, with
  defaults, is unchanged; prove it with the existing walk-forward tests and the fixture double run.

### C. Metrics (plan §4)

1. **Deflated Sharpe ratio** (Bailey and López de Prado, 2014), new function in
   `aitrader.metrics` (for example `deflated_sharpe(...)`), exactly:
   - `sr` = per-session Sharpe (mean / sample stdev of daily returns, **not** annualised) of the
     chained test equity;
   - `T` = number of daily returns; `g3` = sample skewness; `g4` = sample kurtosis (not excess);
   - `V` = sample variance of the per-session training Sharpes of **every** grid variant in
     **every** fold of this run (from the same daily returns used for `sharpe_rf0`, not
     annualised). If fewer than 2 values exist or `V == 0`, the DSR is `null`;
   - `N` = `variants_tried_total`;
   - `SR0 = sqrt(V) × ((1 − γ) × Φ⁻¹(1 − 1/N) + γ × Φ⁻¹(1 − 1/(N·e)))`, with
     γ = 0.5772156649 (Euler–Mascheroni) and e Euler's number;
   - `DSR = Φ( (sr − SR0) × sqrt(T − 1) / sqrt(1 − g3·sr + (g4 − 1)/4 · sr²) )`;
   - `null` when `N < 2`, `T < 3`, the stdev is 0, or the term under the root is ≤ 0.
   - Numbers: do the arithmetic in `Decimal`. `Φ` and `Φ⁻¹` may use `statistics.NormalDist`
     on a float conversion. This is the only float use; it must not touch money or prices, and
     the result is formatted as a string with 4 decimals like the other metrics. Add a short
     comment saying why.
   - Tests: a hand-computed case (write the expected value in the test from a worked calculation
     in a comment), `N = 1 → null`, `V = 0 → null`, more trials give a lower DSR, and symmetric
     returns with zero skew match the closed form.
2. The chained walk-forward summary gets `risk.deflated_sharpe` and `risk.sharpe_per_session`.
3. **B0 Sharpe:** the B0 benchmark block gets `sharpe_rf0` from B0's daily equity, in both single
   backtests and walk-forward.
4. **Per-fold table:** each fold in `walkforward.json` already holds test metrics. Add a compact
   top-level `fold_summary` list with, per fold: test `total_pct`, `trades.profit_factor`,
   `activity.exposure_pct` and the selected grid point (`top_n`, `skip`).

### D. Docs

- `docs/dev/setup.md` (or the research section that documents `walkforward`): the three new
  options, with one example per v1.1 family:
  1. baseline (no options, `--prior-variants 5`);
  2. C1 (`--cash-proxy SHY`);
  3. C2 (`--risk-per-trade-pct 1.0`);
  4. C1+C2 (both options).
  Say that C2 runs need the owner's approval (plan §1).
- Document 12 §6: one line saying the deflated Sharpe is implemented (TASK-012), with the
  formula's source.

## Out of scope

- Running the real-data research (the operator runs it after the merge, and the owner approves
  C2 first).
- Any change to `config/*.yaml`, `hard_caps.py`, `risk/` or the execution path.
- Volatility-targeted sizing, new signals, universe changes.

## Acceptance criteria

1. With all new options absent, every existing test passes unchanged, and the fixture backtest
   double run from `AGENTS.md` is byte-identical to before this task. Its run ID may change only
   if the manifest gains fields; say so in LAST_REPORT if it does.
2. Tests for each case listed in A, B and C. The override is refused above the hard cap and at
   zero or below, and is recorded in the manifest.
3. A fixture walk-forward with `--cash-proxy SHY --risk-per-trade-pct 1.0 --prior-variants 5`
   runs on `tests/fixtures/bars`, if SHY is in the fixture set. If it is not, use a fixture
   symbol and state which one. Its manifest shows both options and `variants_tried_total`.
4. The DSR implementation matches the formula above (tests).
5. All checks in `AGENTS.md` are green; coverage stays at 100% where it is 100% today and never
   below 90% for any package.
6. No change to protected paths or configuration files (`git diff --stat` in LAST_REPORT).
