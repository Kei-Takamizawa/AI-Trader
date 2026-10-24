# CURRENT_TASK — TASK-007a: point-in-time adjusted signals, raw fills, dividends as cash

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.

Design sources: document 12 §2 (row "Adjusted-price leakage": signals use split- and
dividend-adjusted series computed only from information available at the time; fills use the
unadjusted price of that day; dividends are booked as cash), document 03 §2 (`portfolio` books
dividends), `docs/tasks/TASK-006.md` (the `raw/` and `adjusted/` series written by
`aitrader data fetch`).

No new dependency, no network, no credential and no protected path are needed. If you find that
one is needed, stop with BLOCKED as `AGENTS.md` says.

## Why

`aitrader data fetch` stores two series per symbol: `raw/` (prices actually traded) and
`adjusted/` (Alpaca `adjustment=all`, back-adjusted from the download date). Neither can be used
alone:

- **Raw prices for signals** show a split as a crash and every ex-dividend date as a small drop;
  stops, RSI and the large-move check (DQ-3) react to moves that did not cost anything.
- **Adjusted prices for signals and fills** use information from the future: a dividend paid in
  2024 lowers every price before it, so a 2018 decision would see 2018 prices that nobody saw in
  2018, and fills, stops and position sizes would be at prices that never traded.

This task makes the backtest use, at each session, a history adjusted only for the corporate
actions known at that session, while fills, quotes, volumes for the participation cap and equity
marks stay on raw prices, and dividends arrive as cash.

## Definitions

For one symbol, with sessions present in both series:

- `r_t = raw_close_t / adjusted_close_t`, and for consecutive sessions `factor_t = r_t / r_{t-1}`.
- Classification of session `t` (the ex-date), with the existing constants
  `RATIO_TOLERANCE = 0.0005` and `SPLIT_THRESHOLD = 0.2` from `aitrader.data.ingest`:

  | Condition | Kind |
  |---|---|
  | `abs(factor_t - 1) <= RATIO_TOLERANCE` | no action (rounding of the adjusted prices) |
  | `abs(factor_t - 1) > SPLIT_THRESHOLD` | `SPLIT` (also reverse splits) |
  | `1 - SPLIT_THRESHOLD <= factor_t < 1 - RATIO_TOLERANCE` | `DISTRIBUTION` |
  | `1 + RATIO_TOLERANCE < factor_t <= 1 + SPLIT_THRESHOLD` | `UNEXPLAINED` |

- Cash per share of a distribution: `D = raw_close_{t-1} * (1 - factor_t)`, quantized to
  0.000001 USD (ROUND_HALF_EVEN). This follows from the usual adjustment multiplier
  `1 - D / raw_close_{t-1}` for all sessions before the ex-date. It is an estimate from the two
  price series, not the declared amount.
- **Point-in-time (PIT) history as of the close of session `t`:** for every bar `s <= t`,
  `open, high, low, close = raw_s * M_t(s)`, where `M_t(s)` is the product of `factor_e` over all
  detected actions `e` with `s < e <= t`, quantized to 0.0001 (ROUND_HALF_EVEN). Volume:
  `raw_volume_s` divided by the product of the `SPLIT` factors only (distributions do not change
  share counts), rounded to a whole share (ROUND_HALF_EVEN). Bars with no action after them up to
  `t` are the raw bars unchanged; in particular the last bar of every PIT history is the raw bar.

Build the PIT history from the raw bars and the detected factors (not by rescaling the adjusted
series): the result is the same up to rounding, it is exactly raw between actions, and it can be
cached per action segment.

## Scope

1. **New module `src/aitrader/data/adjusted.py`** (export the public names from
   `aitrader.data`):
   - `CorporateActionKind` (`StrEnum`: `DISTRIBUTION`, `SPLIT`, `UNEXPLAINED`) and
     `CorporateAction` (frozen: `symbol`, `session`, `kind`, `factor`, `cash_per_share`, which is
     zero unless `DISTRIBUTION`).
   - `CorporateActionError(DataError)`.
   - Detection as defined above. `aitrader.data.ingest.corporate_actions` must use the same
     detection (one implementation). Its entries in `fetch_report.json` keep their format; the kind
     strings are `"split"`, `"distribution"` and the new `"unexplained"`.
   - `PointInTimeBars(raw: DailyBarSource, adjusted: DailyBarSource | None)`. With `adjusted=None`
     there are no actions and `history()` returns exactly `raw.history()`. Otherwise both sources
     must have the same symbols and, per symbol, the same sessions; any difference is a
     `DataError` naming the symbol and the first differing session.
   - Methods: `symbols`, `actions(symbol)`, `actions_on(session)` (sorted by symbol),
     `history(symbol, as_of)` (bars whose session closed at or before `as_of`, as
     `DailyBarSource.history`), `snapshot_hash` (raw) and `signal_snapshot_hash` (adjusted, or
     `None`).
   - Performance: the real data has 23 symbols, about 2,700 sessions each and up to 12
     distributions a year per symbol. Build each scaled series once per (symbol, number of
     actions so far) and cache it; never rebuild a history every session. Scaled bars may be made
     with `Bar.model_copy(update=...)`: a positive factor and monotone rounding keep the OHLC
     invariants (a test must check that they hold).

2. **Backtest engine (`src/aitrader/backtest/engine.py`):**
   - `run_backtest(settings, strategy, source, *, adjusted: DailyBarSource | None = None, ...)`.
     `source` (raw) stays the only input for quotes, fills, the volume participation cap and
     equity marks. The engine builds `PointInTimeBars(source, adjusted)` and uses its `history()`
     wherever it uses `source.history()` today: the `histories` passed to `cycle.execute` and the
     `StrategyContext.history`.
   - **Dividends:** at the start of each session, after settlement and before the open, for each
     `DISTRIBUTION` on that session in a symbol held at that moment (that is, at the previous
     close): `gross = quantity * D` rounded to the cent (ROUND_HALF_EVEN); US withholding
     `gross * us_pct / 100`, Japanese withholding `(gross - us) * jp_pct / 100`, each rounded to
     the cent (ROUND_HALF_EVEN). Book it with `Ledger.record_dividend` (deterministic entry ID,
     for example `div-<SYMBOL>-<YYYY-MM-DD>`) and credit the same amounts to the `SimBroker`
     through a new method `SimBroker.credit_dividend(...)` that books the same entry in the
     simulator's own ledger, so broker cash and engine cash stay equal. If `gross` rounds to zero,
     nothing is booked. The cash counts as settled on the ex-date (in reality it is paid later;
     say so in the docstring). Protective stops are not changed on ex-dates.
   - **Fail closed:** if a `SPLIT` or `UNEXPLAINED` action falls on session `t` in a symbol that is
     held at the previous close or has a pending intent for `t`, raise `CorporateActionError`
     naming the symbol, session, kind and factor. Otherwise such an action only changes the
     signal history.

3. **Settings:** `BacktestSettings.dividend_us_withholding_pct` (default `10`) and
   `dividend_jp_withholding_pct` (default `20.315`), both between 0 and 100. Add both to
   `config/backtest.yaml` with a comment that they are assumptions to confirm with the broker's
   tax documents (US-Japan treaty rate for dividends; Japanese income and resident tax in a
   tokutei account with withholding, decision Q9). `src/aitrader/config/hard_caps.py` is not
   touched.

4. **Benchmark and results (`src/aitrader/metrics/performance.py`,
   `src/aitrader/backtest/manifest.py`):**
   - `buy_and_hold` accepts the benchmark's distributions (session to cash per share) and books
     them as cash with the same withholding, so the benchmark is not missing its dividends. With
     no distributions its result is unchanged.
   - `metrics.json` gains `dividends_gross_usd` and `withholding_tax_usd` (from
     `Ledger.trading_pnl`). Existing keys keep their names and meaning.
   - The manifest keeps `data_snapshot_hash` (raw) and gains `signal_data_snapshot_hash`
     (`null` without an adjusted series) and `corporate_actions` (counts per kind inside the
     backtest period).

5. **CLI `aitrader backtest --data-dir`:** if the directory has both `raw/` and `adjusted/` (the
   layout `aitrader data fetch` writes), use them; if it has only one of the two, exit with code 4
   and say which is missing; otherwise read the flat layout as today (no adjustments).
   `CorporateActionError` exits with code 4 and its message.

6. **Docs:** update the backtest part of `docs/dev/setup.md` (both layouts; remove the "Until it is
   merged" warning); add a one-line "Implemented in TASK-007a" note under the table in document 12
   §2. Do not change other design documents.

## Out of scope

- Exact declared dividend amounts and pay dates from a corporate-actions endpoint (later; the
  designer must verify that API first).
- Reinvesting dividends, adjusting stops on ex-dates, supporting splits in held positions.
- Strategy S1 and the walk-forward runner (TASK-007b); the research report on real data
  (TASK-007c, run by the operator).
- Risk limits, the order gate, hard caps, LIVE, Webull.

## Acceptance criteria

1. Detection: tests at, just below and just above `RATIO_TOLERANCE` and `SPLIT_THRESHOLD` for each
   kind, and a hand-calculated `D` (for example raw close 100.00 before the ex-date and
   `factor = 0.99` gives `D = 1.000000`).
2. No look-ahead (hypothesis property test): generate a raw series and random distributions and
   splits (factors away from the classification boundaries), build the adjusted series the way a
   vendor does (back-adjusted from the last session, rounded to 0.0001). For any session `t`:
   the PIT history as of `t` matches the series adjusted only for the actions up to `t` within
   0.01% per price; it is the same (within 0.01%) whether the vendor data was "downloaded" at `t`
   or later with more actions after `t`; its last bar equals the raw bar; OHLC invariants hold.
3. Without an adjusted series nothing changes: the fixture backtest from `AGENTS.md`
   (`--data-dir tests/fixtures/bars --start 2023-01-03 --end 2024-12-31`) gives an `equity.csv`
   byte-identical to the one produced before your change, and every value in `metrics.json` that
   existed before is unchanged (only new keys are added). Run it into `bt-before/` before you
   change any file (git-ignored), compare at the end, show the comparison in the report and
   delete the directory.
4. Dividends: an engine test on synthetic raw and adjusted series built in `tmp_path` where a held
   ETF goes ex-dividend: engine and broker cash rise by the same net amount; gross dividend and
   both withholdings match a hand calculation; `aitrader.reconcile.compare_cash` finds no
   difference; a symbol not held, or bought on the ex-date itself, receives nothing.
5. Splits: a held symbol with a split gives `CorporateActionError` (and exit code 4 through the
   CLI, with the message); a symbol that is not held runs through, and its signal history shows no
   jump at the split (no DQ-3 large-move finding there).
6. The buy-and-hold benchmark receives its distributions (test with a hand calculation).
7. CLI: both directories, only one of them (exit 4), and the flat layout are tested.
8. Reproducibility: a backtest on the synthetic raw/adjusted data run twice gives byte-identical
   output files (test), and the fixture double run from `AGENTS.md` passes `diff -r`.
9. All checks in `AGENTS.md` are green; coverage stays at 100% where it is 100% today and never
   below 90% for any package.

## Notes

- Existing pieces to reuse: `DailyBarSource` (`data/bars.py`), `RATIO_TOLERANCE`,
  `SPLIT_THRESHOLD` and `corporate_actions` (`data/ingest.py`), `Ledger.record_dividend` and
  `Ledger.trading_pnl` (`portfolio/ledger.py`), `SimBroker.fund` as a pattern
  (`broker/sim/broker.py`), `run_backtest` (`backtest/engine.py`), `build_manifest` and
  `metrics_for` (`backtest/manifest.py`), `buy_and_hold` (`metrics/performance.py`),
  `BacktestSettings` (`config/settings.py`), the `backtest` command (`cli/main.py`).
- Respect the import contracts (`uv run lint-imports`); do not loosen them. `aitrader.data` must
  not import the engine, the portfolio or the broker.
- No real market data in tests or in the repository: build synthetic series in the tests.
- When done, write `.ai/LAST_REPORT.md` exactly as `AGENTS.md` describes.

## Review round 1 (designer, 2026-10-24) — required change before merge

Commit `ec88c5a` meets most of the spec, but the property test
`test_no_lookahead_across_vendor_download_dates` fails for some inputs. It failed once in a
normal full run on Linux, and with `max_examples=3000` Hypothesis finds this case every time:

```
closes=[80, 80, 88, 80, 80, 80, 80, 80],
factors=['2', '2', '0.98', '0.98', '0.98', '2', '2'],
AssertionError: assert Decimal('63') == Decimal('62')   # PIT volume vs expected
```

**Cause.** A `SPLIT` factor detected from the closes inherits the 0.0001 rounding of the adjusted
series. For example, it can come out as 1.99998 instead of exactly 2. The PIT volume divides by
the product of those inexact factors, so `1000 / 16 = 62.5` becomes 62.50x and rounds to 63
instead of 62. Real splits are exact integer ratios (2:1, 3:2, 1:10, ...), so the volume should be
divided by the exact ratio. CI would fail at random on this, so it blocks the merge.

**Required change (`src/aitrader/data/adjusted.py`, one implementation used by ingest and PIT):**

1. When a factor is classified `SPLIT`, snap it to the nearest fraction `p/q` with
   `1 <= p, q <= 100` (for example `fractions.Fraction(factor).limit_denominator(100)`), and check
   the numerator bound too.
   - If `abs(p/q / factor - 1) <= RATIO_TOLERANCE`, the action is a `SPLIT` with the exact ratio.
   - Otherwise reclassify it as `UNEXPLAINED`, so it fails closed when held or pending.
2. Keep the exact ratio on `CorporateAction`, for example as `split_ratio: tuple[int, int] | None`
   (`(p, q)`, set only for `SPLIT`). `factor` keeps its meaning; for a `SPLIT` it becomes
   `Decimal(p) / Decimal(q)`.
3. Compute the PIT volume with exact integer arithmetic: `raw_volume * prod(q) / prod(p)` over
   the `SPLIT` actions after the bar, then round to a whole share with ROUND_HALF_EVEN, for
   example with `Fraction` and then `Decimal`. Prices use the snapped factor for splits and the
   detected factor for distributions, as before.
4. Add the failing case above to the property test as a `@hypothesis.example(...)`. Add unit tests
   for the snap: a 2:1 split detected as 0.50001 gives `(1, 2)`; a 1:10 reverse split gives
   `(10, 1)`; a 3:2 split gives `(2, 3)`; a factor such as 0.6180 with no fraction within tolerance
   becomes `UNEXPLAINED`. Do not lower `max_examples` and do not relax any assertion.
5. `fetch_report.json` keeps its format; a reclassified action shows as `"unexplained"`.

Not required in this round (keep out of scope):

- the `ResourceWarning` in `test_storage.py`, which is a separate follow-up;
- splits in the buy-and-hold benchmark;
- the cost of rebuilding the cached series per action.

Acceptance: all criteria of TASK-007a still hold. The new tests pass, and the property test passes
with a temporary local run at `max_examples=3000` (report the command and result; do not commit
that change). Update `.ai/LAST_REPORT.md` with a "Review round 1" section.

### Review round 1, finding 2 (CI on `ec88c5a`) — also required

CI fails on `ec88c5a` deterministically:
`FileNotFoundError: [Errno 2] No such file or directory: '.pytest_cache/tmpvg2yk2ey'`.
The cause is that `test_no_lookahead_across_vendor_download_dates` creates its files with
`TemporaryDirectory(dir=".pytest_cache")`, and a fresh checkout has no `.pytest_cache` (CI does
not use the pytest cache). Use a location that always exists. Either take `tmp_path_factory`
(session-scoped, so Hypothesis allows it) and call `mktemp()` per example, or use
`TemporaryDirectory()` with no `dir`. Do not depend on the current directory. Check this locally
from a clean state: `rm -rf .pytest_cache && uv run pytest -p no:cacheprovider tests/unit/data`.

### Review round 1, correction to finding 1 step 1 (designer) — replaces the snap rule

The operator and Codex were right: with `p, q <= 100`, fractions are so dense that almost any
factor (0.6180 snaps to 55/89) falls within `RATIO_TOLERANCE`, so `UNEXPLAINED` could never
happen. Use this rule instead. Real splits are `n:1`, `1:n` or small ratios such as 3:2.

- **Allowed split ratios:** `n/1` and `1/n` for `2 <= n <= 100`, plus `p/q` for `1 <= p, q <= 10`.
- A factor classified `SPLIT` snaps to the allowed ratio whose relative difference
  `abs(ratio / factor - 1)` is at most `RATIO_TOLERANCE`. No two allowed ratios are that close to
  each other, so the match is unique. If no allowed ratio is within the tolerance, the action is
  `UNEXPLAINED`.
- Required tests (designer-checked values): `0.50001 -> (1, 2)`, `10 -> (10, 1)`,
  `0.66667 -> (2, 3)`, `1.5 -> (3, 2)`, `0.04 -> (1, 25)`; `0.6180` and `0.7310 -> UNEXPLAINED`.

Everything else in finding 1 (steps 2 to 5) and finding 2 is unchanged.
