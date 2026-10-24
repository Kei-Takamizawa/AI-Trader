# LAST_REPORT — TASK-008, review round 7

Status: DONE

## Summary
Implemented F3 and F4 on the existing round-6 sandbox adapter. FAILED orders with executed
quantity now map to CANCELLED, preserving FAILED and any error code in BrokerOrder.reject_code;
zero-fill failures remain REJECTED. All five limited query operations now pace inside call()
before acquiring their window, and wait_seconds discards expired calls before calculating a delay.
Regression tests use fake transports, injected waits and simulated clocks, including the real
OrderManager and Ledger.

Task / execution: TASK-008, review round 7; owner Mac (Darwin), Python 3.14.7.
Branch: `claude/project-thread-whk5dv`; starting and final HEAD:
`34c958e9a337926d1773ca9489c0cb761e78e411`.
The worktree was clean at start. AGENTS.md, CLAUDE.md, CURRENT_TASK (all rounds), project status,
design index, the response reference and relevant existing code/tests were read. CURRENT_TASK
already contains the full instructions and was preserved. No next cycle was started.

## Files changed
- `.ai/LAST_REPORT.md`
- `src/aitrader/broker/webull/broker.py`
- `src/aitrader/broker/webull/parsing.py`
- `tests/integration/test_webull_fills.py`
- `tests/unit/broker/test_webull.py`

## Commands run
All uv commands used `UV_OFFLINE=true UV_NO_SYNC=true UV_CACHE_DIR=.cache/uv` to prevent
synchronization/downloads and keep the cache inside the repository. No dependency was installed.

- `uv run ruff check`: PASS, all checks passed.
- `uv run ruff format --check`: PASS, 159 files already formatted.
- `uv run mypy`: PASS, no issues found in 158 source files.
- `uv run lint-imports`: PASS, 9 contracts kept, 0 broken; 124 files, 502 dependencies.
- `uv run pytest --cov=aitrader`: PASS, **1308 passed in 30.74s**.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90`: PASS, **100%** in every
  reported module; 4823 statements and 1200 branches, none missed or partially covered.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`:
  PASS, run ID `bt-628ad003492c7cea`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`:
  PASS, same run ID.
- `diff -r bt-a bt-b && rm -rf bt-a bt-b`: PASS, exit 0, byte-identical. Both paths were
  confirmed absent before running; only the newly generated directories were removed.
- `uv run ruff format src/aitrader/broker/webull/broker.py src/aitrader/broker/webull/parsing.py tests/unit/broker/test_webull.py tests/integration/test_webull_fills.py`:
  PASS, 4 files unchanged.
- `uv run pytest tests/unit/broker/test_webull.py tests/integration/test_webull_fills.py tests/contract --no-cov`:
  initial run FAIL, 4 failed / 236 passed. The new pacing test incorrectly passed client_order_id
  to four SDK request types that do not accept it. Corrected the test to supply that parameter
  only for detail; the subsequent full suite above passes all five parameter cases.
- `git diff --check`: PASS.

## Acceptance criteria
- [x] AC 1, broker contracts — full suite passes for SimBroker and fake-backed WebullBroker,
  including submit/status/idempotency/cancel, lifetime fill-ID union, known/unknown rejection,
  submit ambiguity/lookup, auth/rate errors, step/ID validation, mappings and host rejection.
- [x] AC 2, sandbox isolation — existing host/environment, resolved-host read-back and
  initializer-not-called-on-wrong-host tests pass. Routing and SDK initialization are unchanged.
- [x] AC 3, secret safety — existing captured DEBUG logging, exception, repr and report tests,
  including invented token redaction, pass. Parsing still receives redacted order snapshots.
- [x] AC 4, no submit retries — existing timeout tests pass, including one transport attempt
  and lookup of the unknown submit outcome. Submit code is unchanged.
- [x] AC 5, sandbox CLI — existing fake-transport outcome, token-fact, auth-stop, report and
  missing-variable tests pass. Round 6's SPY flag check remains fail/unavailable until the
  instrument shape is mapped in a later task; an otherwise successful full probe exits 1.
- [x] AC 6, SDK isolation — all 9 import contracts pass without changes.
- [x] AC 7, required checks and coverage — all pass; every module remains at 100% statement
  and branch coverage.
- [x] AC 8, fixture reproducibility — both run IDs match and recursive diff exits 0.
- [x] F3, FAILED after a partial fill —
  `test_failed_after_partial_fill_is_booked_once_and_cancelled` uses the real manager and ledger:
  working partial snapshot books nothing; FAILED books exactly 0.1 once, ends CANCELLED, and
  three repeated syncs return no events or MISMATCH. Existing terminal-snapshot tests cover
  history, detail and open-order parsing for zero, partial and full quantities.
- [x] F3, preserved failure reason —
  `test_failed_status_preserves_reason_and_maps_executed_quantity` covers zero, smallest supported
  positive step, partial and full quantity, with absent/empty/present codes. reject_code is
  `FAILED` or `FAILED:<error_code>`; status mapping keeps the real filled quantity.
- [x] F4, every limited query is paced — `test_limited_calls_wait_before_transport` covers
  balance, positions, detail, open and history. Three calls make exactly three transport calls,
  waiting 2 seconds before the third. The separate pagination wait was removed; the existing
  four-page pacing regression still passes with exactly one 2-second wait.
- [x] F4, expired calls — `test_wait_seconds_ignores_expired_calls_in_staggered_window` checks
  staggered timestamps: expired calls do not consume slots or hide the delay of the next active
  window. Existing threshold tests cover just below, at and just above 2 seconds.
- [x] F4, manager integration — `test_sync_paces_detail_queries_for_three_active_orders`
  successfully queries all three IDs with no events or RateLimitedError, keeps all three active,
  and records exactly one 2-second injected wait. No real sleep is used in these new tests.

## Expected / Actual and reproduction
Expected: a partially executed FAILED order closes through a legal state transition, with no
lost or duplicate execution; consecutive limited queries wait rather than exposing a local
rate-limit error to OrderManager.sync(). Actual: both integration regressions pass, all five
limited operations wait before sending, and remote HTTP/SDK rate-limit error tests still pass.

Reproduce with the offline environment settings above:
`uv run pytest tests/unit/broker/test_webull.py tests/integration/test_webull_fills.py tests/contract --no-cov`.

GUI verification: NOT_RUN; no GUI behavior is involved.
Errors/warnings: the initial test-construction error is documented above and fixed. No warnings
or errors were reported by the final required checks.

## Deviations from the task or the design
None. The old test expecting a local query refusal was replaced by the required pacing test;
remote refusal and direct limiter threshold guards remain tested. No checks were weakened.

The round-5 accounting assumption remains unchanged: fill/cost timestamps use the start-of-poll
observation time, while settlement derives from the execution/placement time in America/New_York.
Working partial quantities remain hidden until terminal; stable :final IDs prevent rebooking.
This is offline behavior evidence, not a measured real-world polling-delay guarantee.

SDK logging suppression is unchanged. SDK_LOGGERS still covers webull, webull.core, webull.data,
webull.core.client, webull.core.http.response, webull.core.retry.retry_condition,
webull.core.auth.signers.app_key_signer, webull.core.auth.composer.default_signature_composer,
webull.core.http.initializer.client_initializer, webull.core.http.initializer.token.token_storage,
webull.core.http.initializer.token.token_manager, webull.data.internal.quotes_client,
webull.trade.events.signature_composer and webull.trade.trade_events_client.

## Open issues and questions
No new implementation blocker or design decision. Instrument mapping and the existing cash,
position and observation-time assumptions remain deferred as specified. Owner approval of the
protected Webull path remains required before merge.

Intentionally NOT_RUN: real sandbox/API calls (operator only), GUI checks, network access,
credential/token-file inspection, dependency installation, production/LIVE/PAPER routing changes,
ledger/manager/BrokerPort changes, design-document edits, Git staging/commit/push/branch changes,
and another cycle. Git work remains the operator's responsibility under AGENTS.md.
