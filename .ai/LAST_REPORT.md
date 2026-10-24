# LAST_REPORT — TASK-007a

Status: DONE

## Summary
Completed Review round 1, finding 1 with the designer's corrected allowed-ratio rule, and
finding 2. Shared corporate-action detection now snaps splits only to approved exact ratios;
unmatched factors become unexplained actions. PIT volumes use rational arithmetic with exact
ties-to-even rounding, and the property test no longer depends on `.pytest_cache` existing.

Cycle: 1, Review round 1; implementer host: macOS (Darwin arm64), Python 3.14.7.
Starting branch: `claude/project-thread-whk5dv`.
Starting HEAD: `48379403e2f602c9ac6344f4714c0e96f957f6d3`; initial working tree was clean.
Read `AGENTS.md`, `CLAUDE.md`, the full task and relevant design/status documents. The complete
instructions already existed in `.ai/CURRENT_TASK.md` and were retained without edits.

## Files changed

- `.ai/LAST_REPORT.md`
- `src/aitrader/data/adjusted.py`
- `tests/unit/data/test_adjusted.py`
- `tests/integration/test_adjusted_backtest.py`

## Commands run

All uv commands used `UV_OFFLINE=1 UV_CACHE_DIR=.uv-cache`. No network access or dependency
installation was needed.

- `uv run ruff format src/aitrader/data/adjusted.py tests/unit/data/test_adjusted.py tests/integration/test_adjusted_backtest.py`
  — PASS: 3 files already formatted after the final code changes.
- `uv run ruff check` — PASS: all checks passed.
- `uv run ruff format --check` — PASS: 139 files already formatted.
- `uv run mypy` — PASS: no issues in 138 source files.
- `uv run lint-imports` — PASS: 9 contracts kept, 0 broken.
- `rm -rf .pytest_cache && uv run pytest -p no:cacheprovider tests/unit/data`
  — PASS: 98 passed in 3.18 seconds, with the cache directory absent.
- Temporarily replaced only `@settings(max_examples=60, deadline=None)` with
  `@settings(max_examples=3000, deadline=None)` and ran
  `uv run pytest -p no:cacheprovider tests/unit/data/test_adjusted.py::test_no_lookahead_across_vendor_download_dates`
  — PASS: 1 passed in 43.54 seconds (3,000 generated examples plus the explicit regression).
  A Python `try/finally` wrapper restored the original file immediately afterward;
  `max_examples=60` is retained in the final diff. Assertions were unchanged.
- `uv run pytest --cov=aitrader` — PASS: 973 passed, 1 warning in 11.46 seconds.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90` — PASS:
  3,862 statements and 938 branches; none missed. Every reported module remains at 100%.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-before --start 2023-01-03 --end 2024-12-31`
  — PASS before any file edits: `bt-6bdb9e53258d07d9`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`
  — PASS after changes: `bt-6bdb9e53258d07d9`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`
  — PASS: same run ID.
- `diff -r bt-a bt-b` and `diff -r bt-before bt-a` — PASS: all output files byte-identical.
- `cmp bt-before/equity.csv bt-a/equity.csv` and Python equality comparison of the complete
  parsed `metrics.json` objects — PASS: equity bytes and all metrics unchanged.
- Removed `bt-before`, `bt-a` and `bt-b` after the comparisons.
- `git diff --check` — PASS: no whitespace errors.

## Review round 1

Finding 1 (including the correction): allowed ratios are deduplicated reduced fractions from
`n/1`, `1/n` for 2 through 100 and `p/q` for 1 through 10. The shared detector compares exact
relative differences against the existing tolerance, stores `(p, q)` only for detected splits,
and uses `Decimal(p) / Decimal(q)` as their price factor. PIT volume accumulates `q/p` with
`Fraction`, rounds that exact rational to an integer with ties-to-even, then converts to
`Decimal`. This avoids decimal approximation even for nonterminating ratios such as 2/3.
Ingest still delegates to this detector; fetch-report serialization has not changed.

Finding 2: `TemporaryDirectory()` now uses its default location, without a relative directory
argument. The data suite passed after explicitly removing `.pytest_cache` and disabling the
cache provider.

Expected / Actual: the reported multi-split example now preserves the exact 62.5-share tie
and returns 62; approved ratios snap correctly, and 0.6180/0.7310 remain unexplained. The
3,000-example run, exact-ratio unit tests and held/pending integration cases all passed.

## Acceptance criteria

- [x] Corrected split rules and report compatibility —
  `test_split_snaps_only_to_allowed_ratios` checks all seven designer-specified values,
  100:1 and 1:100 bounds, rejected out-of-range values, exact stored ratios/factors, zero
  split cash and matching ingest output. Existing threshold tests still check exact and
  neighboring classification boundaries; split factor expectations now reflect snapping.
- [x] Exact volume rounding — the required failing input is an explicit `@example` on
  `test_no_lookahead_across_vendor_download_dates`. The independent price/volume/OHLC
  assertions are unchanged. `test_nonterminating_split_ratio_rounds_volume_exactly`
  checks four ties under 2/3, including rounds both up and down to even.
- [x] No look-ahead — temporary 3,000-example run passed; normal limit remains 60.
- [x] Fail closed — `test_held_and_pending_unsupported_actions_fail_closed` additionally
  checks reclassified 0.6180 and 0.7310 actions for both held and pending positions.
- [x] Fresh-checkout compatibility — 98 data tests passed without `.pytest_cache` and
  with `-p no:cacheprovider`.
- [x] Original TASK-007a behavior preserved — the full suite passes, including dividend
  cash/tax reconciliation, ex-date eligibility, benchmark dividends, raw execution,
  unsupported-action CLI exit code 4, both/partial/flat layouts and synthetic dual-series
  reproducibility. Original detection, source matching and PIT caching tests also pass.
- [x] Fixture compatibility and reproducibility — both post-change runs and the pre-change
  baseline have byte-identical outputs; all metric values are identical.
- [x] All six mandatory checks pass; every module retains 100% statement/branch coverage.

## Deviations from the task or the design

None. Existing boundary expectations for split factors were changed to the exact snapped
values required by the correction; classification and other assertions were retained.

## Open issues and questions

No blocking issues or designer decisions remain.

- One `ResourceWarning` for an unclosed SQLite connection was emitted during
  `tests/unit/storage/test_storage.py::test_crash_after_the_broker_accepted_recovers_from_the_database`.
  This is the explicitly deferred storage warning; no storage code was changed.
- GUI verification: NOT_RUN; CLI/library changes were verified with offline automated tests.
- Intentionally not implemented: benchmark split support and cache rebuild optimization,
  as excluded from this review round. Original dividend/pay-date/tax assumptions are unchanged.
- Intentionally not run: network/data downloads, real-market-data research, live trading,
  protected-path edits, Git commit/push/branch changes. The operator handles Git recording
  under `AGENTS.md` and `CLAUDE.md`.
