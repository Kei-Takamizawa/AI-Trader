# LAST_REPORT — TASK-008, review round 5

Status: DONE

## Summary
Completed the retained round-4 changes under the designer's round-5 rules R1–R3.
Webull emits one terminal cumulative fill per order with a stable `:final` ID, using the
start-of-poll observation time for both the fill and its costs. Working orders expose SUBMITTED
with zero filled quantity and no average price; sale settlement still uses the actual execution's
New York trade date and the injected settlement-date function. Real Ledger/OrderManager tests now
pass for BUY/SELL, late cancelled partial executions, repeated delivery, and terminal-status races.

Task / execution: TASK-008, review round 5; owner Mac (Darwin), Python 3.14.7.
Branch: `claude/project-thread-whk5dv`; starting and final HEAD:
`87127acfb90592e57e21ea0173c407c8a1a4f519`.
The worktree already contained the round-4 implementation, tests and BLOCKED report; these were
preserved and continued as explicitly required. `.ai/CURRENT_TASK.md` already held the complete
instructions including round 5; it was read fully and left unchanged. No other task was started.

## Files changed
The following list includes the retained round-4 work and this round's changes relative to HEAD.

- `.ai/LAST_REPORT.md`
- `src/aitrader/broker/webull/broker.py`
- `src/aitrader/broker/webull/parsing.py`
- `src/aitrader/cli/main.py`
- `tests/contract/test_brokers.py`
- `tests/contract/test_sandbox_check.py`
- `tests/contract/webull_fake.py`
- `tests/unit/broker/test_webull.py`
- `tests/unit/broker/test_webull_tokens.py`
- `tests/integration/test_webull_fills.py` (added)

## Commands run
All uv commands used `UV_OFFLINE=true UV_NO_SYNC=true UV_CACHE_DIR=.cache/uv` to prevent
synchronization/downloads and keep the cache in the repository. No dependency was installed.

- `uv run ruff check`: PASS, all checks passed.
- `uv run ruff format --check`: PASS, 159 files already formatted.
- `uv run mypy`: PASS, no issues found in 158 source files.
- `uv run lint-imports`: PASS, 9 contracts kept, 0 broken; 124 files, 502 dependencies.
- `uv run pytest --cov=aitrader`: PASS, **1264 passed in 26.65s**.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90`: PASS, **100%** in every
  reported module; 4790 statements and 1186 branches, none missed or partially covered.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`:
  PASS, run ID `bt-f29d12382c61def6`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`:
  PASS, same run ID.
- `diff -r bt-a bt-b && rm -rf bt-a bt-b`: PASS, exit 0, byte-identical; only the two newly
  generated output directories were removed. Both were confirmed absent before creating them.
- `uv run ruff format`: PASS, 3 files reformatted, 156 unchanged.
- `uv run pytest tests/integration/test_webull_fills.py tests/contract tests/unit/broker -q --no-cov`:
  initial FAIL on the obsolete same-timestamp redelivery assertion; updated to verify a newer
  observation time with identical execution content and stable ID, as R1 requires.
- `uv run pytest tests/integration/test_webull_fills.py tests/contract tests/unit/broker --no-cov`:
  intermediate FAIL, 1 failed / 255 passed. The new cancel-race fixture incorrectly marked a
  fully executed order CANCELLED while expecting the manager to retain that state. Corrected
  the fixture to a genuine partial cancellation (0.1 of 0.3); the final full suite above passes.
- `uv run pytest tests/unit/broker/test_webull.py::test_history_overlap_clamps_at_six_month_boundary --no-cov`:
  PASS, 3 passed in 1.40s. This was run after refining the boundary offsets to milliseconds,
  matching the API request's timestamp precision.
- `git diff --check`: PASS.

## Acceptance criteria
- [x] AC 1, common contracts for both brokers — the full suite passes all contract cases.
  `test_partial_and_final_fills` checks the union by fill ID across partial and repeated final
  polls, with no over-booking and final total equal to the final quantity. SimBroker exposes its
  partial quantity; Webull exposes SUBMITTED / zero / no average until terminal.
- [x] AC 2, sandbox isolation — host/environment rejection tests and
  `test_wrong_resolved_host_never_calls_initializer` pass. No host guard was relaxed.
- [x] AC 3, secrets — existing credential/token log, exception, repr and report tests pass,
  including `test_token_is_redacted_from_sdk_logs_errors_status_and_report` and
  `test_secret_values_never_reach_output_repr_exceptions_or_report`.
- [x] AC 4, submit not retried — single-attempt SDK timeout and unknown-outcome contract tests
  pass, including `test_real_sdk_http_path_logging_errors_and_single_timeout`.
- [x] AC 5, sandbox CLI — all fake-backed outcome/report, token-fact, skipped-auth and
  exit-code 0/1/2 tests pass. Raw Webull status strings remain available through the existing
  observer metadata; normalized BrokerOrder fields do not alter those report strings.
- [x] AC 6, SDK import isolation — all 9 import contracts pass unchanged.
- [x] AC 7, required checks and coverage — all green; every module remains at 100% statement
  and branch coverage.
- [x] AC 8, fixture reproducibility — two backtests have identical run IDs and recursive diff
  exits 0.
- [x] F1 / R1, terminal-only fills and cursor safety —
  `test_only_terminal_nonzero_snapshots_produce_fills` covers all six documented statuses and
  zero/partial/full quantities, including cancelled zero fills;
  `test_cancelled_partial_older_than_cursor_is_booked_once` uses the real ledger and manager,
  books the older execution once, and emits no MISMATCH. Seven-day overlap, six-month clamp,
  the old-cursor refusal and 50-page cap are covered by unit tests.
- [x] F2, sale settlement — `test_manager_books_buy_then_sell_once_with_settlement` verifies
  one ledger execution per BUY/SELL, quantities, unsettled sale cash and settlement date.
  `test_observation_time_across_pages_costs_and_settlement` verifies a common poll-start time
  across pages and costs, New York date conversion, timestamp fallback and BUY settlement None.
  CLI construction supplies `TradingCalendar().settlement_date`.
- [x] R2 / R3, status/history race —
  `test_terminal_status_before_history_fill_waits_for_next_sync` passes for both SUBMITTED and
  CANCEL_PENDING. No fill is booked when only detail sees the terminal order; the next sync
  books it once, reaches the terminal state and produces no MISMATCH.

## Expected / Actual and reproduction
Expected: terminal cumulative snapshots book exactly once, older executions do not violate
chronological ledger application, and working snapshots cannot force an unbooked partial state.
Actual: all four integration cases pass using real Ledger and OrderManager with handwritten fake
SDK responses. No ledger, manager or BrokerPort change was needed.

Reproduce offline:

```bash
UV_OFFLINE=true UV_NO_SYNC=true UV_CACHE_DIR=.cache/uv uv run pytest tests/integration/test_webull_fills.py --no-cov
```

GUI verification: NOT_RUN; this task concerns a broker adapter and CLI.
No warnings were emitted by the final required checks. Intermediate test failures above were
resolved without weakening checks or the approved lifetime-fill property.

## Deviations from the task or the design
None. The approved round-5 accounting assumption is explicit: fill/cost timestamps represent
observation at the start of a history poll, not exchange execution time. Re-delivery has a newer
observation timestamp and the same fill ID, so the existing ledger-ID check drops it before
booking. Only settlement uses the execution timestamp (or placement timestamp fallback), converted
to America/New_York. Working partial exposure remains invisible until the order is terminal;
this is the designer-approved limitation recorded as U17, not a claim of real-time fill accuracy.
Observation delay depends on successful polling; no real-world timing guarantee was measured.

The existing SDK log suppression is unchanged. Installed SDK source was inspected locally;
its logger names are suppressed through `SDK_LOGGERS`:
`webull`, `webull.core`, `webull.data`, `webull.core.client`, `webull.core.http.response`,
`webull.core.retry.retry_condition`, `webull.core.auth.signers.app_key_signer`,
`webull.core.auth.composer.default_signature_composer`,
`webull.core.http.initializer.client_initializer`,
`webull.core.http.initializer.token.token_storage`,
`webull.core.http.initializer.token.token_manager`, `webull.data.internal.quotes_client`,
`webull.trade.events.signature_composer`, `webull.trade.trade_events_client`.

## Open issues and questions
No unresolved implementation blocker. Real sandbox execution remains NOT_RUN and reserved for
the operator; these results establish offline adapter behavior, not real API acceptance or fill
realism. Owner approval of the Webull protected path remains required before merge.

Intentionally not performed: network/API calls, inspecting real credentials or token files,
production/LIVE/PAPER routing changes, dependency installation, design-document changes,
protected risk/gate/hard-cap changes, Git staging/commit/push/branch changes, and another cycle.
The required setup documentation and document-14 automation note already exist and were preserved.
