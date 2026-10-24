# LAST_REPORT — TASK-008, review round 6

Status: DONE

## Summary
Implemented the seven sandbox findings from review round 6 on top of the committed round-5
adapter. Both cancellation spellings now map to CANCELLED, unused position fields may be absent,
and instruments reports capture shape names only while SPY flags remain explicitly unavailable.
The sandbox check avoids cancelling terminal orders and probes the non-marketable fractional rule
with 1.5 shares. Submit resolves OPENAPI_REPEAT_REQUEST by client-order-ID lookup without placing
again; the three observed business codes are classified as known.

Task / execution: TASK-008, review round 6; owner Mac (Darwin), Python 3.14.7.
Branch: `claude/project-thread-whk5dv`; starting and final HEAD:
`5a2aa267fa5a0b026fa307ae2463a81759465edd`.
The worktree was clean at start. AGENTS.md, CLAUDE.md, the full CURRENT_TASK, project status,
design index, document 14's sandbox results, the pinned response reference, and relevant existing
code/tests were read. Installed SDK boundary source was inspected locally. CURRENT_TASK already
contains all instructions including round 6 and was left unchanged. No other task was started.

## Files changed
- `.ai/LAST_REPORT.md`
- `src/aitrader/broker/webull/broker.py`
- `src/aitrader/broker/webull/check.py`
- `src/aitrader/broker/webull/known_codes.py`
- `src/aitrader/broker/webull/parsing.py`
- `tests/contract/test_brokers.py`
- `tests/contract/test_sandbox_check.py`
- `tests/contract/webull_fake.py`
- `tests/unit/broker/test_webull.py`
- `tests/unit/broker/test_webull_tokens.py`

## Commands run
All uv commands used `UV_OFFLINE=true UV_NO_SYNC=true UV_CACHE_DIR=.cache/uv` to prevent
synchronization/downloads and keep the cache inside the repository. No dependency was installed.

- `uv run ruff check`: PASS, all checks passed.
- `uv run ruff format --check`: PASS, 159 files already formatted.
- `uv run mypy`: PASS, no issues found in 158 source files.
- `uv run lint-imports`: PASS, 9 contracts kept, 0 broken; 124 files, 502 dependencies.
- `uv run pytest --cov=aitrader`: PASS, **1289 passed in 26.71s**.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90`: PASS, **100%** in
  every reported module; 4813 statements and 1194 branches, none missed or partially covered.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`:
  PASS, run ID `bt-f260d57e34f35a87`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`:
  PASS, same run ID.
- `diff -r bt-a bt-b && rm -rf bt-a bt-b`: PASS, exit 0, byte-identical. Both paths were
  confirmed absent before running; only these newly generated directories were removed.
- `uv run ruff format`: PASS; formatting applied to the changed files.
- `uv run pytest tests/contract tests/unit/broker tests/integration/test_webull_fills.py --no-cov -q`:
  PASS, 269 tests (before the final shape and terminal-state test additions).
- `git diff --check`: PASS.

## Acceptance criteria
- [x] AC 1, broker contracts — all common cases pass for SimBroker and fake-backed WebullBroker,
  including the lifetime fill-ID union property. New
  `test_duplicate_place_race_resolves_by_client_id` passes for both brokers; the Webull case
  proves the call sequence detail/place/detail/place/detail and returns the existing ack.
- [x] AC 2, sandbox isolation — existing environment, host allow-list, resolved-host read-back,
  and initializer-not-called-on-wrong-host tests all pass. No routing or guard was changed.
- [x] AC 3, secret safety — credential/token log, exception, repr and written-report leak tests
  pass. Instrument shape names are redacted before observation; body values are not recorded.
- [x] AC 4, no submit retries — existing single-attempt timeout tests pass. Duplicate recovery
  performs only a lookup after rejection; `test_repeat_request_without_matching_order_keeps_rejection`
  proves that a missing order preserves the original known rejection without another place.
- [x] AC 5, sandbox CLI — fake-backed outcome, report, auth-stop, token-fact and CLI tests pass.
  In accordance with round 6 item 3, SPY flags now always record fail/unavailable pending a later
  mapping task, even if the other probes pass. Consequently a complete authenticated sandbox
  check currently exits 1; missing variables still exit 2. A full-run exit-0 case is no longer
  reachable while SPY flags deliberately fail. Earlier CLI success expectations were updated
  to this explicit round-6 requirement, without removing token/result assertions.
- [x] AC 6, SDK isolation — all 9 import contracts pass, unchanged.
- [x] AC 7, mandatory checks and coverage — all green, every module remains at 100% statement
  and branch coverage.
- [x] AC 8, fixture reproducibility — recursive diff exits 0 and both run IDs match.
- [x] Round 6 item 1 — fake cancellation returns observed `CANCELED`; common cancellation
  contracts pass. `test_only_terminal_nonzero_snapshots_produce_fills` includes both spellings,
  zero/partial/full quantities, order status and open-orders mapping, and terminal fill delivery.
- [x] Round 6 item 2 — `test_observed_positions_optional_fields` covers absent, null and empty
  fields, unchanged cost mapping, and false field-presence findings. Other required position
  fields and numeric validations remain enforced; unused optional values are ignored.
- [x] Round 6 item 3 — `test_instrument_shape_names_only_and_flags_unavailable` checks object
  and array roots, sorted top-level/first-item names, empty lists, non-object list items,
  missing lists and malformed roots. Synthetic body values never enter the report. The first
  root-level list is selected without searching later lists if it is empty.
- [x] Round 6 item 4 — `test_observed_reject_codes_are_known` covers all three observed codes;
  each table entry includes its meaning and document-14 section-8.1 source link.
- [x] Round 6 item 5 — `test_whole_terminal_status_never_sends_cancel` covers CANCELED,
  CANCELLED, FILLED and FAILED; no cancel is sent. Existing cancel-race/failure tests still pass.
- [x] Round 6 item 6 — `test_observed_cancel_and_fractional_probe_payloads` proves the submitted
  quantities are 1.00000, 1.50000 and 0.00001 (plus the duplicate whole-share probe), checks the
  fractional rejection result, and proves only one cancellation for the normal whole-order flow.
  Unexpectedly accepted fractional probes remain covered by existing cancellation tests.
- [x] Round 6 item 7 — duplicate-place recovery contract and missing-order regression above
  pass; place_once remains a single attempt so the operator's duplicate probe still observes
  the real OPENAPI_REPEAT_REQUEST code.

## Expected / Actual and reproduction
Expected: observed cancellation/position responses parse, already terminal orders are not
cancelled twice, a duplicate submit can resolve its previous acceptance, and unknown instrument
shapes produce useful metadata without claiming flags are supported.
Actual: all offline tests pass, with one cancellation in the normal sandbox probe flow and
SPY flags explicitly fail/unavailable. The shape tests verify names only and omit synthetic values.

Reproduce offline:

```bash
UV_OFFLINE=true UV_NO_SYNC=true UV_CACHE_DIR=.cache/uv uv run pytest tests/contract tests/unit/broker tests/integration/test_webull_fills.py --no-cov
```

GUI verification: NOT_RUN; this task concerns a broker adapter and CLI.
Errors/warnings: none in final required checks; no failing test run in this cycle.

## Deviations from the task or the design
None. Round 6 supersedes the older expectation of a fully passing sandbox report: instrument
flags deliberately remain unavailable and cause exit 1 until a later task maps the response.
The required setup section and document-14 automation note already exist and were preserved.

The round-5 accounting assumption remains: fill/cost timestamps use the start-of-poll observation
time; only settlement uses the execution/placement timestamp converted to America/New_York.
Working partial quantities remain hidden until terminal. Delayed/repeated history snapshots are
de-duplicated through their stable :final IDs. This is offline behavior evidence, not a measured
real-world polling-delay guarantee.

SDK logging suppression is unchanged. Logger names in SDK_LOGGERS are:
`webull`, `webull.core`, `webull.data`, `webull.core.client`, `webull.core.http.response`,
`webull.core.retry.retry_condition`, `webull.core.auth.signers.app_key_signer`,
`webull.core.auth.composer.default_signature_composer`,
`webull.core.http.initializer.client_initializer`,
`webull.core.http.initializer.token.token_storage`,
`webull.core.http.initializer.token.token_manager`, `webull.data.internal.quotes_client`,
`webull.trade.events.signature_composer`, `webull.trade.trade_events_client`.

## Open issues and questions
No implementation blocker or new design decision is pending. Instrument response mapping is
explicitly deferred by round 6; the next operator run can provide names-only shape findings for
the designer. The meaning of cash/position fields and real fill behavior remain the previously
recorded assumptions. Owner approval of the Webull protected path is required before merge.

Intentionally NOT_RUN: real sandbox/API calls (operator only), GUI checks, network access,
credential/token-file inspection, dependency installation, production/LIVE/PAPER routing changes,
ledger/manager/BrokerPort changes, design-document edits, Git staging/commit/push/branch changes,
and another cycle. Git operations remain the operator's responsibility under AGENTS.md.
