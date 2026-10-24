# LAST_REPORT — TASK-008, review round 3

Status: DONE

## Summary
The sandbox adapter now runs SDK 3.0.4's own `ClientInitializer.initializer` after validating the
resolved UAT host and assigning a private temporary token directory. Token checks use a
10-second duration and 5-second interval; initialized and pending tokens join the in-memory
redaction set. The sandbox command now writes safe token facts and stops after initialization
or SB-1 authentication failure, marking every remaining check `skipped_auth` without further
API calls. All required checks passed offline, including the existing broker contracts and
byte-identical fixture backtests; actual sandbox authentication remains for the operator to verify.

Task / execution: TASK-008, review round 3, owner Mac (Darwin), Python 3.14.7.
Starting branch: `claude/project-thread-whk5dv`.
Starting and final HEAD: `ff1ef94085a020174af8feb97cae1f332185da10`.
The initial worktree was clean. The complete designer instructions were already present in
`.ai/CURRENT_TASK.md`; they were read fully and preserved unchanged. No Git mutation was performed.

## Files changed
- `.ai/LAST_REPORT.md`
- `docs/dev/setup.md`
- `src/aitrader/broker/webull/broker.py`
- `src/aitrader/broker/webull/check.py`
- `src/aitrader/broker/webull/sdk.py`
- `src/aitrader/broker/webull/settings.py`
- `src/aitrader/cli/main.py`
- `tests/unit/broker/test_webull.py`
- `tests/unit/broker/test_webull_tokens.py` (added)

## Commands run
All uv commands used `UV_OFFLINE=true UV_NO_SYNC=true UV_CACHE_DIR=.cache/uv` to use the installed
environment without dependency synchronization, downloads or the external uv cache.

- `uv run ruff check`: PASS, all checks passed.
- `uv run ruff format --check`: PASS, 158 files already formatted.
- `uv run mypy`: PASS, no issues in 157 source files.
- `uv run lint-imports`: PASS, 9 contracts kept, 0 broken; 124 files and 500 dependencies analyzed.
- `uv run pytest --cov=aitrader`: PASS, **1237 passed in 26.94s**, no warnings reported.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90`: PASS, every reported module
  has **100% statement and branch coverage**; total 4784 statements, 1184 branches, none missed.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`:
  PASS, run ID `bt-4afa6c7b1b50d74d`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`:
  PASS, same run ID.
- `diff -r bt-a bt-b && rm -rf bt-a bt-b`: PASS, exit 0, no differences; generated directories removed.
- `uv run pytest tests/unit/broker tests/contract --cov=aitrader.broker.webull --cov-report=term-missing`:
  intermediate targeted run PASS, 227 passed; all Webull modules 100%. Two subsequent CLI token-fact
  cases are included in the final full run above.
- `uv run ruff format`: PASS, applied formatting before the final checks.
- `git diff --check`: PASS.

Intermediate test failures were fixed before the final run: an existing SDK-path test needed
its fake config response to delegate subsequent trading calls to the actual SDK method, and
a new secret-safety fixture needed an explicit whole-share quantity. Initial lint and typing
findings in the new tests were corrected. No checks, thresholds or assertions were weakened.

## Acceptance criteria
- [x] **AC 1 — common BrokerPort contracts**: the full suite passes, including every existing
  parametrized SimBroker and fake-backed WebullBroker contract case.
- [x] **AC 2 — UAT boundary, including initializer guard**:
  `test_wrong_resolved_host_never_calls_initializer` proves neither the initializer nor HTTP
  transport is called for a production-resolved client.
  `test_host_is_checked_again_immediately_before_initializer` changes resolution when the token
  directory is assigned and verifies refusal and directory cleanup before initialization.
  Existing production-host, non-sandbox-environment and send-time endpoint tests also pass.
  Initializer calls re-check resolution and pin each request to the allowed UAT host.
- [x] **AC 3 — credentials and tokens stay secret**:
  `test_token_is_redacted_from_sdk_logs_errors_status_and_report` sets root logging to DEBUG,
  drives successful submit plus 401, 417 and 500 through the actual SDK response/error path,
  and scans logs, exception strings, repr and the written report for all three invented
  credentials and an invented access token. It also verifies redaction of a token echoed as
  an unknown order status. Initialization error and CLI report tests check token safety as well.
- [x] **AC 4 — no submit retries**: the existing unknown-submit-outcome and actual SDK
  single-timeout-attempt tests pass. `auto_retry=False` is preserved during initialization and
  trading; no token-request implementation or trading retry loop was added.
- [x] **AC 5 — sandbox CLI/report, authentication stop and token facts**:
  `test_config_auth_failure_stops_cli_and_writes_report` verifies HTTP 401 and 403, both as SDK
  server exceptions and returned responses: one config request, exit 1, token failure code,
  latency and HTTP status recorded, every other check skipped, private directory removed.
  `test_sb1_auth_stops_every_remaining_call` covers 401 and 403 at accounts, balance, positions
  and instrument probes: the failing call is last and no order is sent.
  `test_cli_success_reports_only_token_facts` covers token-enabled and token-disabled exit-0
  reports; the token check contains configuration enabled/unknown, status, expiry presence,
  error code, HTTP status and latency without token contents or expiry values.
  Existing B2 outcomes and missing-variable exit-2 tests pass.
- [x] **AC 6 — SDK import isolation**: all import contracts and architecture tests pass; no
  import contract was changed.
- [x] **AC 7 — checks and coverage**: all mandatory checks pass; every existing and new reported
  module remains at 100% statement and branch coverage.
- [x] **AC 8 — fixture determinism**: both fixture runs completed and recursive diff returned 0.
- [x] **A1 — SDK token lifecycle and bounded checks**:
  `test_sdk_token_enabled_normal_or_disabled` uses the SDK's real initializer, request builders
  and HTTP signing path with fake HTTP responses. Enabled/NORMAL sends `x-access-token` on the
  next trading call; disabled sends no token header and proceeds. Header assertions retain only
  presence, never token contents. Constructor duration/interval are exactly 10/5 seconds, config
  initialization runs once, the directory is private, and `close()` removes it.
  `test_pending_token_stops_within_bounded_sdk_poll` patches only the SDK token-manager module's
  clock/sleep: two simulated 5-second waits, then `BrokerAuthError` with `ERROR_INIT_TOKEN`, no
  trading calls and no real waiting. The temporary token file/directory is removed on failure.
  Additional tests cover all four requested initializer ClientException codes, I/O and malformed
  response failures, unknown configuration metadata and incomplete token responses.
- [x] **Documentation**: setup now describes initialization, bounded status checks, authentication
  stopping and safe token facts. The existing authorized document-14 automation note is retained.

## GUI verification and Expected / Actual
GUI verification: NOT_RUN; this change concerns a broker adapter and CLI.

Offline CLI operations used CliRunner with invented credentials and fake responses beneath the
real SDK HTTP boundary. Expected / Actual: enabled NORMAL token adds the header; disabled token
omits it; both complete with exit 0 and safe token facts. Config authentication failure exits 1
after exactly one HTTP attempt. SB-1 authentication failure exits 1 and makes no later API call.
Pending token polling ends at a simulated 10 seconds without real waiting. Missing environment
variables still exit 2. All expected outcomes were observed in the final test run.

## SDK logging configuration
The installed SDK source was inspected, including the initializer, TokenManager, token storage,
config/token request classes and ApiClient HTTP signing/error paths. `TradeClient` internals are
not required and `TradeClient` is never constructed. Existing suppression remains active before
SDK construction: each logger below uses level `logging.CRITICAL + 1`, `propagate=False` and a
`logging.NullHandler()`; no new SDK logger name was found for this initialization path.

- `webull`
- `webull.core`
- `webull.data`
- `webull.core.client`
- `webull.core.http.response`
- `webull.core.retry.retry_condition`
- `webull.core.auth.signers.app_key_signer`
- `webull.core.auth.composer.default_signature_composer`
- `webull.core.http.initializer.client_initializer`
- `webull.core.http.initializer.token.token_storage`
- `webull.core.http.initializer.token.token_manager`
- `webull.data.internal.quotes_client`
- `webull.trade.events.signature_composer`
- `webull.trade.trade_events_client`

## Deviations from the task or the design
None. Initialization transport/JSON failures also fail closed as authentication failures, so the
operator receives a safe report instead of proceeding to order probes. The SDK's own pending-token
polling is used unchanged; only its configured duration/interval are bounded as requested.

## Open issues and questions
No unresolved implementation issue or designer decision is required for this offline cycle.

Actual sandbox verification: **NOT_RUN**, reserved for the operator. Fake responses establish
SDK initialization, header construction, safe reporting and stopping behavior; they do not prove
that the operator's current credentials authenticate, that the real UAT token is NORMAL, or that
actual sandbox order rules and responses match the fixtures. The owner/operator still needs to
correct their configured values and run the sandbox command once after review/push, then send
the resulting sanitized report to the designer.

Owner approval remains required in the PR for the task-authorized Webull adapter path. The
operator handles staging, commits, pushes and PRs.

Intentionally not performed: real Webull/API calls, inspection of actual credentials or existing
tokens, dependency installation/downloads, production endpoints, LIVE/PAPER routing changes,
2FA or human-verification flow, streaming/market data, protected risk/gate/hard-cap changes,
build-system changes, Git mutations or a subsequent implementation cycle.
