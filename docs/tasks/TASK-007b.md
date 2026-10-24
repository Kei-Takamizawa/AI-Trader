# CURRENT_TASK — TASK-007b: strategy S1, walk-forward runner, minor vendor adjustments

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-007a.md`.

Design sources: document 11 §3 (S1, the S2 grid note, benchmarks), document 12 §2 (data snooping,
hold-out), §3 (walk-forward protocol), §6 (metrics), §7 (manifest); `docs/status.md`, section
"First real download".

No new dependency, no network, no credential and no protected path are needed. If you find that
one is needed, stop with BLOCKED as `AGENTS.md` says. Month arithmetic is small enough to write by
hand (no `dateutil`).

## Why

Phase P2 needs a research run on real data with a pre-declared, small parameter grid, chosen on
training windows only and reported on the test windows that follow them, while the last 24 months
stay locked (document 12). S2 exists; S1, the walk-forward runner and one data fix are missing.

The data fix: the first real download (2026-10-16) found 16 `UNEXPLAINED` actions, all with factors
between 1.0005 and 1.0009 (XLE 11, XLU 4, XLF 1), which is just above `RATIO_TOLERANCE`. They look
like vendor rounding or small corrections. Under TASK-007a each one would stop a backtest with
`CorporateActionError` whenever the symbol is held, so a 10-year research run could not finish.

## Scope

### A. Minor unexplained adjustments (`src/aitrader/data/adjusted.py`, `backtest/engine.py`)

- New constant `MINOR_ADJUSTMENT_LIMIT = Decimal("0.002")` next to the others.
- Detection does not change: kinds, factors and `fetch_report.json` stay as they are.
- In the engine, an `UNEXPLAINED` action with `abs(factor - 1) <= MINOR_ADJUSTMENT_LIMIT` does
  **not** fail closed. It still changes the signal history, as today, and books no cash. Larger
  `UNEXPLAINED` actions and all unsupported `SPLIT`s fail closed exactly as before. Fills, equity
  and P&L use raw prices, so a minor adjustment changes signals by at most 0.2% and never cash.
- The manifest's `corporate_actions` gains one count, `unexplained_minor`: the `UNEXPLAINED`
  actions in the period within the limit. They are also still counted under `unexplained`.

### B. Next session in the strategy context

- `StrategyContext.next_session: date | None = None`: the exchange session in which intents will
  be executed. The backtest engine fills it from the calendar it already uses (`next_session`).
  Strategies still import only `aitrader.domain` and `aitrader.indicators`.

### C. Strategy S1: trend-filtered momentum rotation (`src/aitrader/strategy/momentum.py`)

`MomentumParams` (frozen dataclass; values as in document 11 §3):

| Field | Default |
|---|---|
| `symbols` | U0 without the cash proxy: SPY, IVV, VOO, VTI, QQQ, IWM, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY, SHY, IEF, TLT, GLD, IAU |
| `equivalents` | `(("SPY", "IVV", "VOO"), ("GLD", "IAU"))`: same index or same asset |
| `lookback` | 126 |
| `skip` | 0 |
| `trend_period` | 200 |
| `top_n` | 3 |
| `atr_period` | 20 |
| `atr_stop_multiple` | 3 |
| `target_weight` | 0.25 |

`MomentumStrategy`, ID `S1`, version `1.0.0`, registered in `STRATEGIES`. Rules:

1. **Weekly review only.** A session is a review session when `ctx.next_session` is `None` or
   falls in a different ISO week (`isocalendar()[:2]`) than `ctx.session`. This makes the review
   the last session of the week. On other sessions return no intents. Protective stops still run
   every session in the engine.
2. **Eligible:** a symbol with enough bars for `sma(trend_period)`, `atr(atr_period)` and
   `total_return(lookback, skip)`, whose last close is above its `sma(trend_period)`.
3. **Ranking:** by `total_return(closes, lookback, skip)` descending, ties by symbol ascending.
   The history is point-in-time adjusted (TASK-007a), so this is a total return including
   distributions.
4. **Equivalents:** at most one member of each `equivalents` group is selected. If a member is
   held and eligible, it represents the group (no switching between equivalent ETFs). Otherwise
   the best-ranked eligible member represents it.
5. **Selected set:** the first `top_n` entries of the ranking after rule 4.
6. **Exits:** each held symbol that is not in the selected set gets an `EXIT` for its whole
   quantity. That covers dropping out of the top ranks, closing below the trend average and no
   longer being eligible. The reason names which of these applies.
7. **Entries:** each selected symbol that is not held gets a BUY with
   `target_notional = equity_usd * target_weight` (to the cent) and
   `stop_price = last_close - atr_stop_multiple * ATR`, to the cent. If that stop is ≤ 0, there is
   no entry. The risk engine then sizes every entry to the same risk per trade (0.5% of equity at
   the stop), which is document 11's "equal risk". `target_weight` is only a cap.
8. **Cash:** when fewer than `top_n` symbols are eligible, the rest stays in cash. A cash proxy (BIL)
   is not used in version 1.0.0; this is a deliberate choice from document 11 ("cash proxy or
   cash"). Stops are set at entry and never loosened, and there is no trailing rule in 1.0.0.

`params()` returns every field as a string, so that `params_hash` changes with any field.

### D. Walk-forward runner (`src/aitrader/backtest/walkforward.py`) and `aitrader walkforward`

- **Grids, declared in code before any run** (document 12 §2: a small, pre-declared grid):
  - S1: `top_n ∈ {3, 2}` × `skip ∈ {0, 5}`, four variants in the order (3, 0), (3, 5), (2, 0), (2, 5).
  - S2: one variant, the defaults.
  - Changing a grid is a new strategy variant and must be visible in the manifest (`grid` and
    `variants_tried`).
- **Windows** (document 12 §3):
  - `data_end` is the benchmark's (SPY's) last raw session.
  - `holdout_start` is the first session after `data_end` minus `holdout_months` (default 24)
    calendar months.
  - Folds start at `start`, the first session on or after `--start`: training covers
    `[s, s + train_months)` (default 36) and testing `[s + train_months, s + train_months + test_months)`
    (default 6), and `s` advances by `test_months`.
  - A fold is used only if its whole test window ends before `holdout_start`. There is no
    truncation, and nothing on or after `holdout_start` is ever run. If no fold fits, raise
    `DataError` (CLI exit 4) with the dates.
- **Selection:** for each fold, run every variant on the training window and pick the one with the
  highest training `sharpe_rf0` (from the existing metrics). Ties go to the higher `total_pct`, then
  the earlier grid order. A variant with a `None` Sharpe ranks below every number.
- **Test:** run the selected variant on the test window. Each run, training or test, starts with
  `initial_capital_usd` and uses all history before its start for indicators, as `run_backtest`
  already does.
- **Combined result:** chain the test windows' daily returns in order into one equity curve, from
  `initial_capital_usd`. Report the same return and risk metrics as `metrics.json` (`returns`,
  `risk`) for the chained curve, plus B0 over the same test sessions.
- **Outputs**, written to `--out-dir`:
  - `walkforward.json`, with per fold the train and test periods, every variant's training
    `sharpe_rf0` and `total_pct` (so that neighbouring values can be compared for "knife-edge"
    optima), the selected variant, and its test `returns`, `risk` and `trades`; plus the combined
    result.
  - `manifest.yaml`, with the git commit, config hash, strategy ID and version, the grid (each
    variant's params hash), `variants_tried` (the grid size), the data and signal snapshot
    hashes, window lengths, `data_end`, `holdout_start`, `holdout_used: false`, and corporate-action
    counts.
  - Same inputs give byte-identical files.
- **CLI:** `aitrader walkforward --data-dir --out-dir --strategy {S1,S2} [--config-dir]
  [--start] [--train-months 36] [--test-months 6] [--holdout-months 24]`, with the same data-dir
  layouts and exit codes as `backtest`. The default `--start` is 2017-01-03, which leaves one year
  of warm-up for a 200-day average plus a 126-day return. The hold-out run itself is **not** part
  of this task.

### E. Docs

- `docs/dev/setup.md`: a short "Walk-forward" section with the command and what the output
  contains.
- Document 11 §3: one line under S1, "Implemented in TASK-007b (version 1.0.0; cash instead of a
  cash proxy; equivalents de-duplicated)".
- Document 12 §3: one line, "Implemented in TASK-007b".
- Do not change other design documents.

## Out of scope

- The hold-out run, the research report and go/no-go decisions (TASK-007c, run by the operator).
- Trailing stops, a cash proxy, volatility targeting, close-of-day execution.
- Deflated Sharpe ratio and bootstrap intervals (TASK-007c, or a later task).
- The data-fetch limit on recent SIP data, and the storage `ResourceWarning`.
- Risk limits, the order gate, hard caps, LIVE, Webull.

## Acceptance criteria

1. **Minor adjustments:** engine tests with a held position.
   - An `UNEXPLAINED` factor of `1.0019` runs through. `1.0021` raises `CorporateActionError`.
   - The `unexplained_minor` count in the manifest is correct.
   - No cash is booked for a minor adjustment.
2. **`next_session`:** an engine test shows the value on a Friday before a Monday holiday (the
   next session is Tuesday) and on a mid-week session.
3. **S1 rules,** each with a unit test on synthetic bars:
   - review versus non-review sessions, including a Thursday before a Good Friday;
   - the eligibility boundary (close equal to the average is not eligible);
   - ranking ties;
   - `skip`;
   - the equivalents rule, including the held-member case;
   - exits with the right reason;
   - entry notional and stop to the cent;
   - fewer than `top_n` eligible leaves the rest in cash;
   - insufficient history gives no intent;
   - `params_hash` changes when any field changes.
4. **S1 end to end:** a backtest on synthetic raw and adjusted series in `tmp_path` (at least five
   symbols, including an equivalents pair, about 400 sessions) trades only on review sessions
   (stop exits aside). Two runs give byte-identical outputs.
5. **Walk-forward windows:**
   - unit tests for the month arithmetic (month ends, leap day) and the fold list for a
     hand-checked case;
   - no fold touches `holdout_start`;
   - "no fold fits" exits 4.
6. **Selection:** a test with a stub strategy whose variants have known training Sharpes checks the
   pick, both tie-breaks and the `None` rule.
7. **Walk-forward end to end:** on synthetic data in `tmp_path` with short windows (for example
   2/1/2 months), the outputs exist, the combined curve equals the chained test returns
   (hand-checked), and two runs are byte-identical.
8. The fixture backtest from `AGENTS.md` still gives a byte-identical `equity.csv` and unchanged
   existing `metrics.json` values compared with the code before your change. Run it into
   `bt-before/` first (git-ignored), compare at the end, report and delete.
9. All checks in `AGENTS.md` are green; coverage stays at 100% where it is 100% today and never
   below 90% for any package.

## Notes

- Reuse `run_backtest`, `PointInTimeBars`, `metrics_for`/`summarize`, `build_manifest` (or a
  shared helper it and the walk-forward manifest both use), `buy_and_hold`, `sma`, `atr`,
  `total_return`, the `backtest` CLI's data-dir handling (factor it into one helper rather than
  copying it), and `make_intent_id` as in `strategy/pullback.py`.
- Keep the import contracts; the strategy must not import the calendar, the engine or the data
  package.
- Running the walk-forward over 10 years of real data must stay practical: the operator runs it
  in TASK-007c. Report the wall time of the synthetic end-to-end test and avoid recomputing
  point-in-time histories across runs that share one data snapshot. For example, build the
  `PointInTimeBars` once and pass it in.
- No real market data in tests or in the repository.
- When done, write `.ai/LAST_REPORT.md` exactly as `AGENTS.md` describes.
