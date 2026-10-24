# LAST_REPORT — TASK-013

Status: DONE

## Summary

Implemented PAPER stage 1 with the existing S1 1.0.0 strategy, daily-bar BACKTEST engine,
risk checks, execution path and SimBroker. The local runner verifies the book identity and all
recorded sessions through the requested date before transactionally appending new sessions;
mismatches publish HALTED and exit 6. Added immutable session snapshots, a SQLite schema-3
migration, atomic status and daily reports, CLI commands and operator instructions.

Cycle: TASK-013, one implementation/verification cycle. Execution terminal: local macOS,
Darwin arm64, Python 3.14.7. Starting branch: `claude/project-thread-whk5dv`; starting and ending
HEAD: `52c9f49e3e0bf86257add98a4f42ddc12b66301b`. The initial working tree was clean.
The complete approved task remains unchanged in `.ai/CURRENT_TASK.md`.

## Files changed

- `.ai/LAST_REPORT.md`
- `config/paper.yaml`
- `docs/dev/setup.md`
- `pyproject.toml`
- `src/aitrader/app/paper.py` (new)
- `src/aitrader/app/paper_reports.py` (new)
- `src/aitrader/backtest/engine.py`
- `src/aitrader/backtest/snapshots.py` (new)
- `src/aitrader/cli/main.py`
- `src/aitrader/config/__init__.py`
- `src/aitrader/config/loader.py`
- `src/aitrader/config/settings.py`
- `src/aitrader/storage/database.py`
- `tests/architecture/test_import_contracts.py`
- `tests/contract/test_sandbox_check.py`
- `tests/integration/test_paper.py` (new)
- `tests/unit/broker/test_webull_tokens.py`
- `tests/unit/cli/test_cli.py`
- `tests/unit/config/test_loader.py`
- `tests/unit/reconcile/test_reconciler.py`

Existing configuration tests now use the required PAPER start and USD capital. Sandbox CLI
tests patch the same adapter/check classes at their defining modules because Webull imports
now occur only inside the sandbox command. The old-schema reconciliation test expects the
latest schema version while preserving its reconciliation assertions. No test was weakened,
skipped, disabled or marked expected-to-fail.

## Commands run

Every successful `uv run` below used `UV_CACHE_DIR=.uv-cache UV_OFFLINE=1` to keep execution
inside the repository and prevent downloads. No new dependency was required or installed.

| Command | Result |
|---|---|
| `uv run ruff check` | PASS — All checks passed |
| `uv run ruff format --check` | PASS — 163 files already formatted |
| `uv run mypy` | PASS — no issues in 162 source files |
| `uv run lint-imports` | PASS — 10 contracts kept, 0 broken |
| `uv run pytest --cov=aitrader` | PASS — 1,412 passed in 46.61 seconds, no warnings |
| `uv run coverage report --include='src/aitrader/*' --fail-under=90` | PASS — every module 100%; total 5,159 statements, 1,270 branches, none missed |
| `uv run pytest tests/integration/test_paper.py --cov=aitrader --cov-report=term-missing:skip-covered` | PASS — 78 PAPER tests; full-suite coverage measured separately above |
| `uv run ruff format` | PASS — formatting applied before final checks |
| `git diff --check` | PASS |

### BACKTEST compatibility

Before changing source or configuration, ran:

```bash
uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir .task013-baseline --start 2023-01-03 --end 2024-12-31
```

After implementation, ran the required fixture backtest twice:

```bash
uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31
uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31
diff -r .task013-baseline bt-a
diff -r bt-a bt-b
```

PASS — both differences exited 0 with no output. All three files (`manifest.yaml`,
`metrics.json`, `equity.csv`) are byte-identical to the pre-task output and to each other.
All runs reported `bt-e572cb2510f8c5eb`, with BACKTEST config hash
`c8059f8f9c3fb693198cf6bbdaa97617e8d4eda91454b8271f066fd054ef9f95`.
The baseline, `bt-a` and `bt-b` were removed after comparison.

Baseline file SHA-256 values:

```text
manifest.yaml 6b97a2351e3d2b7601989327f9bc08ad011c78943a05560da09c0fecfc7bd465
metrics.json  950594da2d70fda4f33126ff75ae96b19a2e3b1bfeeb8699c8f1f0059c56ad35
equity.csv    1ce0a8f6a09cafd3c2e0dcf3be8e1e2919d304af941b8e4eee3bae2cbef39363
```

### PAPER command smoke test

Created a temporary directory inside the repository, copied `config/base.yaml`, and wrote a
PAPER configuration with `paper.start: 2023-10-02`. Invoked the installed `.venv/bin/aitrader`
entry point twice, using the same options:

```bash
aitrader paper run --config <temporary>/config/paper.yaml --data-dir tests/fixtures/bars --state-dir <temporary>/state --through 2023-10-23 --git-commit 52c9f49e3e0bf86257add98a4f42ddc12b66301b
```

Expected / Actual: first run records 16 sessions; second adds none; both exit 0 with identical
equity and last session. Actual outputs:

```text
Run 1: exit=0; RUNNING sessions_added=16 last_session=2023-10-23 equity_usd=321.92328057762
Run 2: exit=0; RUNNING sessions_added=0 last_session=2023-10-23 equity_usd=321.92328057762
```

Temporary configuration, database and reports were removed automatically on exit. All data was
synthetic fixture data; these results do not establish strategy profitability or real fills.

## Acceptance criteria

- [x] Every requested offline behavior and every existing test passes — 1,412 passed;
  all modules retain 100% statement and branch coverage.
- [x] First-run session parity — `test_first_run_parity_and_status` compares every stored
  snapshot and hash with plain `run_backtest`, plus equity, ledger cash, positions and B0.
- [x] Repetition, catch-up and older through dates —
  `test_idempotence_and_catchup_then_older_through` checks rows, report bytes and modification
  times, and verifies that existing later sessions remain intact.
- [x] Tampered hashes, changed snapshots, malformed JSON and rehashed tampering halt without
  new writes — `test_tampered_session_halts_without_any_writes`; actual changed fixture bars
  are covered by `test_changed_market_data_halts_on_first_affected_close`.
- [x] Changed start, simulation, risk, strategy parameters, ID or version needs a new directory
  and exits 6 — `test_changed_book_needs_new_directory`; existing book start remains in status.
- [x] Before-start and closed-day periods record no sessions — `test_not_started` and
  `test_start_on_closed_day_without_sessions`.
- [x] Future through dates, missing SPY and held locks are refused —
  `test_after_data_end_refused_and_default_catches_up`, `test_missing_spy_halts`,
  `test_lock_refused`, `test_cli_errors_and_lock`.
- [x] A corporate action on a held SPY position halts with data exit 4, preserving existing
  rows/reports and recording no sessions in a fresh directory —
  `test_corporate_action_on_held_position_records_nothing`.
- [x] Status, calendar staleness boundaries, fill fees, day P&L, intents, rejections and quality
  findings are covered — `test_first_run_parity_and_status`,
  `test_staleness_uses_last_completed_exchange_session`,
  `test_reports_include_rejections_pending_reasons_and_findings`,
  `test_distribution_uses_same_book_and_b0_tax_settings`.
- [x] Atomic replacement and failure cleanup preserve the previous complete file; existing
  reports are not rewritten — `test_atomic_file_replacement`,
  `test_failed_atomic_write_leaves_old_complete_file`, `test_preexisting_reports_are_preserved`.
- [x] SQLite migration preserves old tables and new sessions roll back as one transaction —
  `test_schema_two_is_upgraded_without_changing_old_tables`,
  `test_new_sessions_rollback_together_on_sqlite_failure`.
- [x] Simulation defaults and numerical boundaries — `test_settings_defaults_and_simulation_validation`
  and `test_simulation_thresholds`; BACKTEST config identity also verified against pre-task output.
- [x] Building and running PAPER never imports Webull —
  `test_cli_and_runner_import_without_webull` runs in a subprocess with a refusing import hook;
  the added import contract and architecture mutation test enforce the boundary.
- [x] Fixture BACKTEST outputs, manifest and run ID are byte-identical to pre-task output —
  both directory comparisons above passed.
- [x] Actual fixture PAPER command records sessions and a second command adds none —
  smoke outputs above; temporary files removed.
- [x] All six mandatory AGENTS.md checks are green.
- [x] No protected path changed — the protected-path `git diff --name-only` check returned
  no output for risk, hard caps, execution gate, Webull adapter and live configuration paths.

## Change boundary

`git diff --stat` captured before writing this report (Git excludes untracked additions):

```text
 config/paper.yaml                           | 13 +++++--
 docs/dev/setup.md                           | 30 +++++++++++++++
 pyproject.toml                              |  6 +++
 src/aitrader/backtest/engine.py             | 53 ++++++++++++++++++++++++--
 src/aitrader/cli/main.py                    | 59 +++++++++++++++++++++++++++--
 src/aitrader/config/__init__.py             |  2 +
 src/aitrader/config/loader.py               | 10 ++++-
 src/aitrader/config/settings.py             | 24 ++++++++----
 src/aitrader/storage/database.py            | 19 ++++++++++
 tests/architecture/test_import_contracts.py |  1 +
 tests/contract/test_sandbox_check.py        |  4 +-
 tests/unit/broker/test_webull_tokens.py     |  3 +-
 tests/unit/cli/test_cli.py                  |  3 +-
 tests/unit/config/test_loader.py            | 32 +++++++++++-----
 tests/unit/reconcile/test_reconciler.py     |  2 +-
 15 files changed, 226 insertions(+), 35 deletions(-)
```

New files, in addition to that tracked diff: `app/paper.py` (273 lines),
`app/paper_reports.py` (121), `backtest/snapshots.py` (65), and
`tests/integration/test_paper.py` (660). Their full paths are listed above. This report is also
modified. No design documents or protected source/configuration files were changed.

## Errors, warnings and GUI verification

The initial unconfigured `uv run` could not open the default user cache due to sandbox
permissions; subsequent commands used the repository-local offline cache and succeeded.
A local editable-package rebuild emitted a uv/build-backend version warning; it completed
without downloading dependencies. Initial development-test failures involved the removed
PAPER field, relocated sandbox mocks and a mistaken test expectation before the market close;
all were corrected and the final full suite produced no warnings or failures.

GUI: NOT_RUN — this task adds a local CLI and files, with no graphical workflow.

## Deviations from the task or the design

None. The runner is under `aitrader.app`, and a new import contract explicitly prohibits
Webull dependencies. The existing schema steps remain unchanged; PAPER tables are step 3.

## Open issues and questions

None requiring designer judgment for TASK-013. Live quotes and actual execution quality remain
outside the evidence provided by the daily-bar simulator.

## Intentionally not performed

No network, remote fetch, credentials, real market data, scheduler, notifications, cloud
resources, deployment, LIVE operation or protected-path changes. No Git staging, commits,
pushes, branch changes or history changes; the operator performs Git work under AGENTS.md.
No subsequent implementation cycle was started.
