# LAST_REPORT — TASK-014

Status: DONE

## Summary

Implemented S3 1.0.0 with fixed core slots, strict weekly trend filtering, defensive SHY allocation,
and deterministic exits, reductions and entries. Added the declared S3 walk-forward grid,
reporting-only neighbour stability and independent-fold cost aggregation for all strategies.
Session snapshots are now opt-in; the PAPER runner explicitly captures them. Updated research
commands and document 11 without changing S1/S2 trading rules or protected paths.

Task / cycle: TASK-014 / cycle 7. Execution platform: operator Mac, local offline repository.
Initial branch: `claude/project-thread-whk5dv`; HEAD: `72fcb6f0b6105a7b61d25fbad9a0e62341c806a1`.
The initial working tree was clean. `.ai/CURRENT_TASK.md` already contained the complete task
and was read fully and retained unchanged. No Git mutation was performed.

## Files changed

.ai/LAST_REPORT.md
docs/design/11-strategy-and-ai-analysis.md
docs/dev/setup.md
src/aitrader/app/paper.py
src/aitrader/backtest/engine.py
src/aitrader/backtest/walkforward.py
src/aitrader/cli/main.py
src/aitrader/strategy/__init__.py
src/aitrader/strategy/trend_core.py
tests/integration/test_paper.py
tests/integration/test_s3_reporting.py
tests/integration/test_walkforward.py
tests/unit/strategy/test_trend_core.py

## Commands run

All successful uv commands used `UV_CACHE_DIR=.uv-cache UV_OFFLINE=1 UV_NO_SYNC=1` to keep
cache writes in the repository and prohibit dependency downloads. Existing installed dependencies
were sufficient. An initial uv invocation using its default cache failed with a sandbox
permission error; rerunning with the repository-local cache resolved it without escalation.

- `uv run ruff check` — PASS, all checks passed.
- `uv run ruff format` — PASS; formatted changed Python files.
- `uv run ruff format --check` — PASS, 166 files already formatted.
- `uv run mypy` — PASS, no issues in 165 source files.
- `uv run lint-imports` — PASS, 10 contracts kept, 0 broken.
- `uv run pytest --cov=aitrader` — PASS, 1,442 passed in 50.04 seconds.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90` — PASS,
  5,275 statements, 1,322 branches, zero missed statements or branches, 100% coverage.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`
  — PASS, run ID `bt-5b263a4573f70625`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`
  — PASS, same run ID.
- `diff -r bt-a bt-b && rm -rf bt-a bt-b` — PASS. Before removal, also compared `bt-a`
  against the saved pre-task backtest: all three output files were byte-identical, including
  manifest, run ID, metrics and equity CSV.
- Fixture walk-forward, before and after, for each of S1 and S2:
  `uv run aitrader walkforward --data-dir tests/fixtures/bars --out-dir <output> --strategy <S1|S2> --start 2023-01-03 --train-months 6 --test-months 3 --holdout-months 3`
  — PASS. Recursively compared every pre-existing JSON field and list entry; all matched.
  Manifests were byte-identical. Only the requested new report fields were added.
- `uv run aitrader walkforward --data-dir tests/fixtures/bars --out-dir <output> --strategy S3 --start 2023-01-03 --train-months 6 --test-months 3 --holdout-months 3 --risk-per-trade-pct 1.0 --prior-variants 21`
  — PASS; also covered by `test_s3_cli_and_cash_proxy_refusal`.
- `uv run aitrader backtest --strategy S3 --data-dir tests/fixtures/bars --out-dir <output> --start 2023-01-03 --end 2024-12-31`
  — PASS, run ID `bt-dc9861285deedc0e`.
- `git diff --check` — PASS.

Intermediate test failures exposed the CLI's separate S1/S2 allow-list, an incorrect expected
cost ratio in a new test, and a new stop test whose flat core prices failed the trend filter.
These were corrected before the successful final checks. No test was disabled or weakened.

### Fixture wall time

Same CLI fixture protocol above, single observations on the operator Mac:

| Strategy | Before task | After task, including neighbours |
|---|---:|---:|
| S1 | 3.043 s | 2.953 s |
| S2 | 2.339 s | 2.233 s |

To isolate snapshot capture from the newly added neighbour work, also timed the final S1
`run_walkforward` with identical fixture parameters and neighbours enabled, wrapping its
backtest call to set `capture_sessions=True` and then `False`: **2.550 s → 1.536 s**
(about 39.8% less wall time). Manifest and complete reports matched exactly between these
runs. These are single fixture measurements, not real-data performance claims.

## Acceptance criteria

- [x] Existing S1/S2 selections, chained equity and metrics unchanged — recursive comparison
  with pre-task fixture reports and byte-identical manifests; full regression suite passes.
- [x] Fixture double backtest is byte-identical to pre-task output — both diffs passed,
  including manifest and run ID.
- [x] S3 cases in scope A — `tests/unit/strategy/test_trend_core.py`: weekly review,
  strict trend boundary, four core buys, two-slot defensive buy, deterministic intent ordering,
  full core exit, full defensive exit, reduction just below/at/above the 5% band, quantity
  rounding to zero, missing/stale bars, short trend/ATR history, zero/negative stops,
  defensive trend bypass, no top-ups, and stable parameter identity.
- [x] Declared grids and options — `test_declared_axes_and_neighbours`, existing S1 grid
  checks, S3 CLI research options, and refusal of S3 cash proxy. Grid order is
  (200, 3), (200, 2), (150, 3), (150, 2); S1 order is preserved.
- [x] Stability and costs — `tests/integration/test_s3_reporting.py`: neighbours match
  independent test runs, diagonal and nonadjacent points are excluded, positive/zero/negative
  signs are distinct, S2 stability is null and excluded from counts, reporting-only neighbours
  leave selection/equity/combined metrics unchanged, and cost aggregation is hand-checked for
  positive, zero and negative gross P&L and annualisation.
- [x] Snapshot capture is opt-in — `test_snapshot_opt_in`; captures and ordinary runs
  have identical equity, intents and metrics. Existing PAPER assertions verify persisted
  snapshot JSON, hashes, fills and reports against replay with explicit capture enabled.
- [x] S3-R1 fixture walk-forward completes — fixture symbols are IWM, QQQ and SPY.
  **GLD and SHY are missing**: GLD is unwanted and SHY cannot be bought, so those slots
  remain in cash under the declared rules.
- [x] All mandatory checks green; coverage remains 100%, including every existing package
  and the new strategy.
- [x] Protected paths and configuration files unchanged — reviewed path list and diff stat.

### Diff stat

`git diff --stat` for tracked implementation files before writing this report:

```text
 docs/design/11-strategy-and-ai-analysis.md |   9 +++
 docs/dev/setup.md                          |  19 +++++
 src/aitrader/app/paper.py                  |   1 +
 src/aitrader/backtest/engine.py            |  75 ++++++++++----------
 src/aitrader/backtest/walkforward.py       | 109 +++++++++++++++++++++++++++--
 src/aitrader/cli/main.py                   |   8 ++-
 src/aitrader/strategy/__init__.py          |   5 +-
 tests/integration/test_paper.py            |   1 +
 tests/integration/test_walkforward.py      |   2 +-
 9 files changed, 184 insertions(+), 45 deletions(-)
```

Untracked additions are listed explicitly under Files changed: the S3 module and its two
new test files. This report is also modified. Temporary baseline, timing and CLI artifacts
were removed after recording the results; they are not deliverables.

## GUI verification

NOT_RUN: this task changes offline strategy and CLI behavior only. Verification used synthetic
unit/integration tests and CLI runs. Expected/actual: deterministic strategy decisions and reports,
unchanged legacy results, and PAPER snapshot parity; all verified as described above.

## Deviations from the task or the design

Two minimal existing-test adaptations were necessary: the PAPER replay fixture explicitly
passes `capture_sessions=True` for the new opt-in API, and the unknown-strategy test uses
`UNKNOWN` instead of the newly valid `S3`. All existing assertions are retained; no checks were
weakened. Product behavior and design otherwise follow the task. S2 retains its existing
`selected_grid_point` null-valued top_n/skip fields to preserve legacy output, while its
empty declared axes correctly yield no neighbours.

## Open issues and questions

None requiring a design decision. No new dependency, external request, credential access,
protected-path edit, configuration edit, LIVE enablement, Git commit, push or branch change.
Real-data research, S3 PAPER adoption and production deployment were intentionally not run.
