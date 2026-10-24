# LAST_REPORT — TASK-007a

Status: DONE

## Summary
Implemented point-in-time signal histories built from raw bars and shared corporate-action
detection, with one cached scaled series per symbol/action segment. Executions, participation
volumes and equity marks continue to use raw data; estimated dividends and sequential US/JP
withholding are booked identically in the engine and simulator ledgers. Added fail-closed
handling for held/pending splits and unexplained actions, dividend-aware benchmarks, result
metadata, dual-directory CLI loading, and the requested documentation.

Cycle: 1; implementer host: macOS (Darwin arm64), Python 3.14.7.
Starting branch: `claude/project-thread-whk5dv`.
Starting HEAD: `776797c9ab414426732e241657f8c13660094fba`; initial working tree was clean.
The existing `.ai/CURRENT_TASK.md` was read fully and retained without edits.

## Files changed

- `.ai/LAST_REPORT.md`
- `config/backtest.yaml`
- `docs/design/12-backtest-and-evaluation.md`
- `docs/dev/setup.md`
- `src/aitrader/backtest/engine.py`
- `src/aitrader/backtest/manifest.py`
- `src/aitrader/broker/sim/broker.py`
- `src/aitrader/cli/main.py`
- `src/aitrader/config/settings.py`
- `src/aitrader/data/__init__.py`
- `src/aitrader/data/adjusted.py`
- `src/aitrader/data/ingest.py`
- `src/aitrader/metrics/performance.py`
- `tests/integration/test_adjusted_backtest.py`
- `tests/unit/data/adjusted_builders.py`
- `tests/unit/data/test_adjusted.py`
- `tests/unit/metrics/test_performance.py`

## Commands run

All successful uv commands used `UV_OFFLINE=1 UV_CACHE_DIR=.uv-cache` (a repository-local,
ignored cache). No dependency was added or downloaded.

- `uv run ruff check` — PASS: all checks passed.
- `uv run ruff format --check` — PASS: 139 files already formatted.
- `uv run mypy` — PASS: no issues in 138 source files.
- `uv run lint-imports` — PASS: 9 contracts kept, 0 broken.
- `uv run pytest --cov=aitrader` — PASS: 954 passed, 1 warning in 11.12 seconds.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90` — PASS:
  3,849 statements, 936 branches, none missed; every reported module is 100% covered.
- `uv run ruff format` and `uv run ruff check --fix` — PASS: applied formatting/import sorting.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-before --start 2023-01-03 --end 2024-12-31`
  — PASS before implementation: `bt-7db436ed963e8974`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`
  — PASS after implementation: `bt-6bdb9e53258d07d9`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`
  — PASS: the same run ID.
- `diff -r bt-a bt-b` — PASS: no differences in any output file.
- `cmp bt-before/equity.csv bt-a/equity.csv` — PASS: byte-identical equity CSV.
- Recursive Python comparison of every pre-existing `metrics.json` key/value — PASS:
  all unchanged; only `pnl.dividends_gross_usd` and `pnl.withholding_tax_usd` were added.
- `git diff --check` — PASS: no whitespace errors.
- Removed the three generated comparison directories after verifying their contents.

Initial execution without a cache override failed because the sandbox could not write to
`~/.cache/uv`. Using the repository-local cache resolved this without escalation or network.
Initial new integration tests revealed that constant-price fixtures fail the existing risk
engine's zero-volatility check; the synthetic warm-up now has nonzero volatility. Temporary
hand-calculation expectations were corrected to the actual raw-price fill model. These test
failures were resolved; no existing test or check was weakened.

## Acceptance criteria

- [x] 1. Detection boundaries and cash estimate —
  `test_detection_thresholds_and_ingest_share_one_implementation` covers both sides and exact
  tolerance/split thresholds, including reverse splits and unexplained factors; factor 0.99
  on a preceding $100 close gives $1.000000. `test_distribution_estimate_half_even` checks
  six-decimal ties.
- [x] 2. No look-ahead — `test_no_lookahead_across_vendor_download_dates` runs 60 Hypothesis
  examples, each with eight cutoffs, random prices, distributions and splits. Fully downloaded
  and truncated vendor snapshots agree with the independent PIT formula within 0.01%; last
  bars equal raw bars, split-only volumes match, and all scaled OHLC bars pass validation.
- [x] 3. Unadjusted compatibility — baseline equity bytes and every pre-existing metric value
  match as described above. Manifest/run ID changes are expected from new settings/metadata.
- [x] 4. Dividends — `test_engine_dividend_cash_taxes_raw_marks_and_benchmark`: 0.39980 held
  shares receive $0.40 gross, $0.04 US tax, $0.07 JP tax, and $0.29 settled net cash; identical
  journal entries exist in both ledgers and `compare_cash` returns no differences. Raw fill
  price is $100.05, ex-date equity uses the raw $99 close, and the $97 stop stays unchanged.
  `test_no_dividend_if_not_held_at_previous_close` verifies no holding and ex-date purchases;
  `test_zero_rounded_gross_is_not_booked` verifies zero-rounded income is omitted.
- [x] 5. Splits — `test_held_and_pending_unsupported_actions_fail_closed` covers held and
  pending forward/reverse splits and unexplained actions. `test_cli_split_error_message`
  verifies exit code 4 with symbol, session, kind and factor and no output directory.
  `test_unheld_split_adjusts_signals_and_volume_without_dq3` verifies an unheld split runs,
  has continuous signal prices, split-adjusted historical volume, and no DQ-3 finding.
- [x] 6. Benchmark distributions — `test_benchmark_dividends_against_hand_calculation`: ten
  shares receive $10 gross less $1 US and $1.83 JP tax; final $990 stock value plus $7.17 cash
  gives $997.17. No first-open dividend is credited; omitted/empty distributions preserve
  the original results. Engine metrics also verify dividend-aware benchmark wiring.
- [x] 7. CLI layouts — `test_cli_requires_both_directories` verifies either missing directory
  exits 4 with its name; `test_dual_series_cli_is_reproducible_and_manifest_counts_period_only`
  verifies dual layout, hashes and period-only action counts. Existing
  `test_cli_backtest_is_reproducible` verifies flat layout.
- [x] 8. Reproducibility — dual-series synthetic output files are byte-identical in the new
  integration test; the full fixture double run passes `diff -r`.
- [x] 9. Required checks and coverage — all six checks pass, with 100% statement/branch
  coverage throughout. Withholding settings have exact and neighboring 0/100 boundary tests.
  History tests also cover mismatched symbols/sessions, close-time visibility, unchanged raw
  segments, sorted same-day actions, half-even split-volume rounding, and cache reuse without
  rebuilding bars within an action segment.

## Deviations from the task or the design

None. The numeric action constants live alongside the shared detector in `data/adjusted.py`
and remain importable from `data/ingest.py`; there is only one detection implementation.

## Open issues and questions

No blocking issues or new design decisions are required.

- The final suite emitted one `ResourceWarning` for an unclosed SQLite connection in the
  existing `tests/unit/storage/test_storage.py::test_transactions_roll_back_on_errors` test,
  attributed to `storage/database.py:143`. It did not fail a check; storage code was unchanged.
- Dividend estimates, ex-date settlement and withholding remain the explicitly requested
  assumptions. Exact declared amounts/pay dates and broker tax-document confirmation remain
  outside this task.
- GUI verification: NOT_RUN; this task exposes CLI/library behavior and was verified through
  offline CLI and automated tests. No GUI was needed.
- Intentionally not run: network/data downloads, real-market-data research, live trading,
  protected-path edits, Git commit/push/branch changes. Git recording belongs to the operator
  under the repository's AGENTS.md and CLAUDE.md.
