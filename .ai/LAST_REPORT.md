# LAST_REPORT — TASK-012

Status: DONE

## Summary

Implemented S1 cash-proxy slots, BACKTEST-only in-memory risk overrides, prior-variant
accounting, deflated Sharpe, B0 Sharpe and a compact per-fold table. Added offline boundary,
formula and integration tests and documented all four research families. All 1,333 tests pass;
every source module retains 100% statement and branch coverage. No protected path or
configuration file changed, and no real-data research or network request was made.

Task / cycle: TASK-012 / cycle 5. Execution: owner Mac, zsh, repository
`/Users/harrison/Documents/AI-Trader`. Starting branch: `claude/project-thread-whk5dv`;
HEAD: `f59eea41267ff499483c23bc71b300072471c218`. Initial working tree was clean.
The task already existed in `.ai/CURRENT_TASK.md` and was read fully and left intact.

## Files changed

.ai/LAST_REPORT.md
docs/design/12-backtest-and-evaluation.md
docs/dev/setup.md
src/aitrader/backtest/walkforward.py
src/aitrader/cli/main.py
src/aitrader/metrics/__init__.py
src/aitrader/metrics/performance.py
src/aitrader/strategy/momentum.py
tests/integration/test_walkforward.py
tests/unit/metrics/test_performance.py
tests/unit/strategy/test_momentum.py

## Commands run

All uv commands used `UV_OFFLINE=1 UV_CACHE_DIR=.uv-cache` to forbid downloads and keep
cache writes inside the repository. No dependency was added.

- `uv run ruff check` — PASS, all checks passed.
- `uv run ruff format --check` — PASS, 159 files already formatted.
- `uv run mypy` — PASS, no issues in 158 source files.
- `uv run lint-imports` — PASS, 9 contracts kept, 0 broken.
- `uv run pytest --cov=aitrader` — PASS, 1333 passed in 32.83 seconds.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90` — PASS;
  4901 statements, 0 missed, 1232 branches, 0 partial; every module 100%.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31` — PASS.
- Same backtest with `--out-dir bt-b` — PASS.
- `diff -r bt-a bt-b` — PASS, exit 0, byte-identical outputs; both run IDs
  `bt-34fad579455c08af`. The generated directories were removed after comparison.
- Before editing, saved the same backtest to `.task012-baseline`; compared it after editing:
  manifest and equity CSV byte-identical; all previous JSON metrics equal. The only new
  metrics field is `benchmarks.B0_buy_and_hold.sharpe_rf0: "1.1743"`.
- Offline S1 reference comparison: loaded momentum.py from starting HEAD with `git show`,
  ran both old/default and new/default S1 on fixture dates 2023-01-03 through 2024-12-31:
  18 intents, 21 fills and 502 equity points exactly equal.
- `uv run aitrader walkforward --data-dir tests/fixtures/bars --out-dir .task012-wf-check --start 2023-01-03 --train-months 6 --test-months 3 --holdout-months 3 --cash-proxy QQQ --risk-per-trade-pct 1.0 --prior-variants 5` — PASS, 4 folds;
  every grid point has `cash_proxy: QQQ`, `risk_override: {risk_per_trade_pct: '1.0'}`,
  `variants_tried: 4`, `variants_tried_total: 9`, `holdout_used: false`.
  Chained DSR `0.0095`, per-session Sharpe `0.1188`, B0 Sharpe `2.0637`.
  These synthetic results exercise code only. SHY is absent from the fixtures; QQQ is the
  task-authorized substitute. Temporary outputs and implementation scripts were removed.
- `git diff --check` — PASS.

## Acceptance criteria

- [x] Default behaviour preserved — existing suite passes; default S1 intents/fills/equity
  equal starting HEAD, and default parameter hash excludes the inactive option. The fixture
  double run is byte-identical; run ID and manifest are unchanged from before the task.
  As explicitly required by scope C3, metrics JSON gains B0 Sharpe, so that file has an
  additive difference from the pre-task output; all its pre-existing values are unchanged.
- [x] Cash-proxy cases — `test_cash_proxy_empty_slots_and_exit` covers zero, one and three
  qualifying ETFs; proxy intents target three/two slots, use ATR stops and exit when full.
  `test_cash_proxy_excluded_from_ranking_and_equivalents` covers exclusion even when held
  and highest-momentum. Missing/stale bars log the cash decision reason; insufficient ATR,
  held proxy and nonpositive stops are also tested. Inactive identity/intents are tested.
- [x] Research options — invalid risk values zero, negative, above cap, NaN, infinity and
  malformed decimals exit 5. Just above zero, just below cap and exactly cap are accepted;
  PAPER overrides are refused. Negative prior counts and S2 cash proxies are refused.
  `test_research_fixture_options_and_summary` verifies manifest fields, unchanged original
  Settings, all grid points, fold summaries and recomputed unrounded training-score DSR.
- [x] Fixture research run — command and manifest evidence above, using QQQ in place of SHY.
- [x] DSR formula — symmetric worked calculation gives `0.6584`; independent asymmetric
  worked moments give `0.6405`. Tests cover N=1, insufficient samples/scores, zero training
  variance, zero return stdev, nonpositive root, and lower DSR with more trials.
  Decimal arithmetic is used except the documented NormalDist CDF/quantile boundary.
- [x] B0 Sharpe — single-backtest hand calculation checks `11.2250`; walk-forward test
  recomputes it from the benchmark daily curve.
- [x] Checks and coverage — all six mandatory checks PASS, every source module 100%.
- [x] Protected/configuration paths unchanged — `git diff --stat` below; only the explicit
  TASK-012 addition to document 12 was made in the design directory.

Implementation diff stat (excluding this report):

```text
 docs/design/12-backtest-and-evaluation.md |   3 +
 docs/dev/setup.md                         |  31 ++++++++
 src/aitrader/backtest/walkforward.py      |  96 ++++++++++++++++++++--
 src/aitrader/cli/main.py                  |  21 ++++-
 src/aitrader/metrics/__init__.py          |   4 +
 src/aitrader/metrics/performance.py       |  42 +++++++++-
 src/aitrader/strategy/momentum.py         |  43 +++++++++-
 tests/integration/test_walkforward.py     | 128 +++++++++++++++++++++++++++++-
 tests/unit/metrics/test_performance.py    |  64 +++++++++++++++
 tests/unit/strategy/test_momentum.py      |  71 ++++++++++++++++-
 10 files changed, 487 insertions(+), 16 deletions(-)
```

## Deviations from the task or the design

No product/design deviation. The existing all-fields-affect-hash test needed a type-aware
extension: the newly added optional None field is changed to SHY rather than adding 1 to
None. Its hash-change assertion is preserved; no test or check was weakened.

Skewness and non-excess kurtosis use empirical central moments standardized by the empirical
second moment (documented in code/setup and worked tests); Sharpe and training-score variance
use sample standard deviation/variance. Undefined training Sharpes are omitted, never replaced
with zero; fewer than two valid values or zero variance yields null DSR.
The strategy port returns order intents only: unavailable-proxy reasons are logged at INFO,
and also used as exit reasons when that proxy is held. No intent is invented for idle cash.

## Open issues and questions

None requiring a design decision for this implementation.
Real-data C2 research remains for the operator after owner approval. To report N=17 with a
four-point grid, pass `--prior-variants 13`; the task's fixture example with 5 records N=9.

GUI verification: NOT_RUN (CLI/pure computation task; no GUI changed).
Network, real market data, credentials, broker endpoints, LIVE and production deployment:
intentionally NOT_RUN. Git commit, push, staging and branch changes: intentionally NOT_RUN
under AGENTS.md; the operator records these changes.

Development-only failures (None arithmetic in the parameter-enumeration test, draft expected
values/enum case in new tests, and formatting/import checks) were corrected and reverified.
Final checks have no unresolved error or warning.
