# LAST_REPORT — TASK-007b

Status: DONE

## Summary
Implemented S1 version 1.0.0 weekly momentum rotation with trend eligibility, deterministic ranking, stable equivalent-ETF selection, and entry stops. Added training-only walk-forward selection over the declared S1/S2 grids, chained test returns, benchmark metrics, deterministic output files, and an untouched hold-out boundary. Minor unexplained adjustments through 0.2% now adjust signal history without booking cash or stopping held positions; larger unsupported actions still fail closed. All required checks pass, all measured source modules retain 100% statement and branch coverage, and existing fixture equity and metrics are byte-identical to the pre-change baseline.

Execution: macOS (Darwin), Python 3.14.7, local repository; no Windows execution.
Branch: `claude/project-thread-whk5dv`.
Starting and unchanged HEAD: `0f6f57bba35689f899396b08cf260aced05db1f7`.
The initial working tree was clean. The complete approved task was already stored in
`.ai/CURRENT_TASK.md`; it was read fully and left unchanged. No commit, push, branch change,
network access, new dependency, real market data, credential access, or protected-path edit
was performed.

## Files changed

- `.ai/LAST_REPORT.md`
- `docs/design/11-strategy-and-ai-analysis.md`
- `docs/design/12-backtest-and-evaluation.md`
- `docs/dev/setup.md`
- `src/aitrader/backtest/engine.py`
- `src/aitrader/backtest/manifest.py`
- `src/aitrader/backtest/walkforward.py`
- `src/aitrader/cli/main.py`
- `src/aitrader/data/adjusted.py`
- `src/aitrader/strategy/__init__.py`
- `src/aitrader/strategy/base.py`
- `src/aitrader/strategy/momentum.py`
- `tests/integration/test_adjusted_backtest.py`
- `tests/integration/test_walkforward.py`
- `tests/unit/strategy/test_momentum.py`

## Commands run

All successful `uv run` commands used `UV_CACHE_DIR=.uv-cache UV_OFFLINE=1` to keep cache
writes inside the repository and prohibit downloads. The initial command using uv's default
cache failed with `Operation not permitted`; using the repository-local cache resolved this.

- `uv run ruff check` — PASS: All checks passed.
- `uv run ruff format --check` — PASS: 143 files already formatted.
- `uv run mypy` — PASS: no issues in 142 source files.
- `uv run lint-imports` — PASS: 9 contracts kept, 0 broken.
- `uv run pytest --cov=aitrader` — PASS: 1,006 tests passed; one existing SQLite ResourceWarning.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90` — PASS: 4,070 statements and 984 branches, no misses or partial branches; 100% for every measured source module.
- `uv run ruff format` and `uv run ruff check --fix` — PASS: formatting/import cleanup during development; no checks were disabled.
- `uv run pytest tests/integration/test_walkforward.py tests/unit/strategy/test_momentum.py -s` — PASS: 29 tests in 8.09 seconds on the final targeted run; the synthetic walk-forward call took **0.891 seconds** (five symbols, over 400 raw/adjusted sessions, 2/1/2-month windows, four S1 variants).
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-before --start 2023-01-03 --end 2024-12-31` — PASS, executed before source changes.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31` — PASS.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31` — PASS.
- `diff -r bt-a bt-b` — PASS: all three output files byte-identical.
- `diff bt-before/equity.csv bt-a/equity.csv` — PASS: byte-identical.
- `diff bt-before/metrics.json bt-a/metrics.json` — PASS: byte-identical, including every existing metric.
- `shasum -a 256` on the compared files — PASS: equity SHA-256 `1ce0a8f6a09cafd3c2e0dcf3be8e1e2919d304af941b8e4eee3bae2cbef39363`; metrics SHA-256 `d4592f8d1aac44388280c685bd1470b9245372f2dda3d95c05658019a83d09ca`, identical before/after.
- `git diff --check` — PASS.
- Removed only the generated `bt-before/`, `bt-a/`, and `bt-b/` comparison directories after verification.

Expected/actual: unchanged S2 fixture equity and metrics, deterministic repeat runs, and
training-only parameter selection were expected; all were observed. The backtest manifest
changes as specified because `corporate_actions.unexplained_minor` is now present.
Early development lint/type errors and a synthetic skip-test fixture that unintentionally
closed exactly on its SMA were corrected before the final successful checks.

GUI operations: NOT_RUN; this task adds offline library and CLI behavior, with no GUI.
Separate build command: NOT_RUN; no independent build command is required by AGENTS.md.

## Acceptance criteria

- [x] 1. Minor adjustments with a held position — `test_minor_unexplained_boundary_and_no_cash` covers 1.0019, exactly 1.002, and 1.0021; allowed runs have identical cash and equity to raw-only runs, and correct nested manifest counts. Existing unsupported-action tests still pass for held and pending positions.
- [x] 2. Next session — `test_next_session_context_includes_monday_holiday` checks Wednesday/Thursday and Friday 2023-01-13 resolving to Tuesday 2023-01-17.
- [x] 3. S1 unit rules — `tests/unit/strategy/test_momentum.py` covers weekly versus non-review sessions, Good Friday, ISO week/year transition, absent next-session fallback, close just above/equal/below SMA, ties, skip, equivalent selection and eligible held representatives, ineligible held representatives, exit reasons/quantity, cent-rounded entry notional and ATR stop, holding without rebuying, cash when fewer qualify, missing history, nonpositive stops, and hash changes for every parameter field.
- [x] 4. S1 end to end — `test_s1_end_to_end` uses five synthetic raw/adjusted symbol series with an equivalents pair and over 400 sessions; fills occur, no equivalent pair is held together, every submitted strategy intent originates on a weekly review, and both runs have identical manifest/metrics/equity bytes.
- [x] 5. Windows — `test_month_arithmetic`, `test_fold_list_and_holdout_boundary`, and `test_cli_errors` cover month ends/leap day, hand-checked fold dates, exclusive ends with no hold-out sessions, positive window lengths, and CLI exit 4 when no fold fits. The end-to-end case also checks the weekend start normalization and strict hold-out boundary.
- [x] 6. Selection — `test_selection_known_scores_and_grid` and `test_runner_selects_stub_variants_on_training_only` check Sharpe priority, total-return tie-breaking, stable grid-order ties, and None below negative numeric Sharpe. Stub test-window scores deliberately differ; training scores still select the test variant. The runner test also verifies a single shared PointInTimeBars instance across folds/variants.
- [x] 7. Walk-forward end to end — `test_walkforward_e2e_chain_and_determinism` checks every combined daily equity value against independently rerun selected test windows, SPY B0 over the same sessions, manifest boundaries/grid size, output existence, and identical bytes on repeat. `test_chain_hand_calculation` verifies 100 -> 110 -> 99, then a new fold's +20% gives 118.8. `test_cli_walkforward` runs both S1 and S2.
- [x] 8. Fixture regression — pre-change and post-change equity.csv and metrics.json have identical bytes and hashes; repeated post-change output directories also match in full and are removed afterward.
- [x] 9. Required checks/coverage — all six required commands pass; every measured source module remains at 100% statement and branch coverage, with no test skipping, weakening, or expected-failure markers added.

## Deviations from the task or the design

None. Month offsets stay anchored to the original fold start, with month-end clamping, to
keep consecutive test windows adjacent. Periods in walkforward.json label their ends
`end_exclusive` so hold-out exclusion is explicit. A single point-in-time snapshot is shared
across all training/test runs and manifest generation. Only the two authorized implementation
notes were added to the design documents.

## Open issues and questions

No designer decision is required for this task.

The existing, out-of-scope SQLite `ResourceWarning: unclosed database` appeared once in the
final full suite (`tests/unit/storage/test_storage.py::test_transactions_roll_back_on_errors`,
`storage/database.py:143`). Reproduction: `UV_CACHE_DIR=.uv-cache UV_OFFLINE=1 uv run pytest --cov=aitrader`;
the warning's collection site can vary with garbage collection. No storage code was changed.

The CLI reads the repository commit using `/usr/bin/git` on this Mac and reports DataError
(exit 4) if commit lookup fails; other host layouts were not verified. The synthetic timing
is a local measurement, not a ten-year real-data performance claim.

Intentionally not run: locked hold-out evaluation, real-data research, go/no-go decisions,
live/broker endpoints, deployment, and operator Git actions, as specified for later cycles.
