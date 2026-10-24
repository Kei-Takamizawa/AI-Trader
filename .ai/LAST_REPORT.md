# LAST_REPORT — TASK-008

Status: DONE

## Summary
Implemented the sandbox-only Webull BrokerPort adapter using the installed official SDK 3.0.4 request classes and the designer's pinned response reference. Added the shared broker contract suite, strict Decimal parsing, guarded UAT endpoint resolution, single-attempt order placement, query limiting, credential redaction and the operator-only `aitrader sandbox check` command. Updated setup documentation and the explicitly authorized one-line automation note in document 14. All mandatory checks pass, every reported module retains 100% statement and branch coverage, and both fixture backtests produce byte-identical output.

Task / cycle: TASK-008 / cycle 4, implementing the complete instructions through designer review round 2. Execution: operator Mac, repository-local terminal in `/Users/harrison/Documents/AI-Trader`; branch `claude/project-thread-whk5dv`, starting HEAD `14fc57b26e98f66590d66188bdfb7706d7db09de`. `.ai/CURRENT_TASK.md` already contained the complete approved task and was preserved unchanged. The initial working tree contained the operator's SDK dependency changes in `pyproject.toml` and `uv.lock`; these were preserved. No Git staging, commit, push, branch change or merge was performed.

## Files changed
Implementation and verification files for the operator to review and commit:

- `.ai/LAST_REPORT.md`
- `docs/design/14-webull-openapi-research.md`
- `docs/dev/setup.md`
- `pyproject.toml` — strengthened adapter/SDK import boundaries; preserved the operator's dependency addition and the existing build-system constraint
- `src/aitrader/broker/port.py` — optional HTTP/code error metadata; account ID excluded from repr
- `src/aitrader/broker/webull/__init__.py` (added)
- `src/aitrader/broker/webull/broker.py` (added)
- `src/aitrader/broker/webull/check.py` (added)
- `src/aitrader/broker/webull/known_codes.py` (added)
- `src/aitrader/broker/webull/parsing.py` (added)
- `src/aitrader/broker/webull/sdk.py` (added)
- `src/aitrader/broker/webull/settings.py` (added)
- `src/aitrader/cli/main.py`
- `src/aitrader/config/loader.py`
- `src/aitrader/config/settings.py`
- `tests/architecture/test_import_contracts.py`
- `tests/contract/__init__.py` (added)
- `tests/contract/conftest.py` (added)
- `tests/contract/test_brokers.py` (added)
- `tests/contract/test_sandbox_check.py` (added)
- `tests/contract/webull_fake.py` (added)
- `tests/unit/broker/test_webull.py` (added)

Related pre-existing operator change: `uv.lock` — SDK 3.0.4 and its resolved dependencies. Codex did not add a third-party dependency or manually edit this file; the operator should include their dependency changes with the implementation.

## Commands run
All uv commands used `UV_OFFLINE=1 UV_CACHE_DIR=.uv-cache`. No external network request, dependency download or real credential inspection was performed. uv rebuilt the existing local project after the import-contract edit; no new third-party package was installed by Codex.

- `git status --short`, `git branch --show-current`, `git rev-parse HEAD`: PASS; initial state recorded above.
- Installed SDK source inspection under `.venv/`: PASS; checked ApiClient, endpoint resolution, request setters, pagination, HTTP response/error behavior, retry handling, token-directory configuration and logger names. Module name is `webull`, so the forbidden SDK module name remains unchanged.
- `uv run ruff format`: PASS; final source formatting unchanged.
- `uv run ruff check`: PASS, all checks passed.
- `uv run ruff format --check`: PASS, 157 files already formatted.
- `uv run mypy`: PASS, no issues in 156 source files.
- `uv run lint-imports`: PASS, 9 contracts kept, 0 broken; 124 files and 495 dependencies analyzed.
- `uv run pytest tests/contract tests/unit/broker/test_webull.py --cov=aitrader.broker.webull --cov-report=term-missing`: PASS, 169 passed; focused adapter checks reached 100% statement and branch coverage. The subsequent lint-only cleanup replaced a try/except/pass with `contextlib.suppress`; the final full run below verifies that final version.
- `uv run pytest --cov=aitrader`: PASS, **1205 passed in 26.74s**, no warnings. All reported modules have **100% statement and branch coverage**: 4694 statements, 1162 branches, none missed.
- `uv run coverage report --include='src/aitrader/*' --fail-under=90`: PASS, total and every reported module 100%.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`: PASS, run ID `bt-7ce2151d21b94cca`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`: PASS, same run ID.
- `diff -r bt-a bt-b && rm -rf bt-a bt-b`: PASS, exit 0, no differences. Both generated output directories were removed after comparison.
- `git diff --check`: PASS.

Intermediate failures were corrected before the final run: a test expected a queued history response to remain after consumption, and a synthetic EUR balance fixture omitted two required fields after strict field validation was added. The fixtures were corrected to represent their intended documented shapes. Initial formatting and typing findings were fixed; no test, assertion, import contract or coverage threshold was disabled or weakened.

Warning: uv reported the previously documented build-system constraint warning (`uv-build >=0.11.0,<0.12` versus uv 0.12.1) while rebuilding the local project. `[build-system]` was intentionally left unchanged, as instructed.

## Acceptance criteria
- [x] **AC 1 — shared BrokerPort contract**: `tests/contract/test_brokers.py` runs the same parametrized suite against SimBroker and fake-backed WebullBroker. It covers submit/status, idempotent resubmit, cancel, partial/final fills, known/unknown rejects, unknown submit outcome followed by lookup, authentication, rate limiting, fractional step boundaries, invalid IDs, cash/position mapping and production refusal. Existing SimBroker behavior and code were preserved.
- [x] **AC 2 — sandbox boundary and resolved-host read-back**: `test_host_rejected_before_client_build`, `test_all_sdk_production_hosts_refused`, `test_non_sandbox_refused`, `test_sdk_resolved_production_host_refused_without_transport`, and `test_real_sdk_client_override_pin_no_retries_and_cleanup`. The only allow-listed host is the documented bare UAT trading host; casing is normalized, while schemes, ports, paths, user info, trailing dots, production and other-service hosts are rejected. Real SDK sends re-check resolution and pin the request endpoint before calling the SDK. New Webull/contract tests explicitly fail on socket connection attempts.
- [x] **AC 3 — secret-safe output**: `test_secret_values_never_reach_output_repr_exceptions_or_report` sets root logging to DEBUG and exercises submit, 401, 417 and 500, then scans logs, exception strings, repr and the written report for all three invented secret values. `test_real_sdk_http_path_logging_errors_and_single_timeout` also exercises the actual SDK response/error logger path with a fake single-request function. Provider messages are redacted in exceptions and never stored in reports; reports contain no account identifiers or raw balances.
- [x] **AC 4 — no submit retry**: `test_unknown_submit_outcome_then_lookup` proves one place call on a timeout after acceptance, followed by a successful client-ID lookup. `test_real_sdk_http_path_logging_errors_and_single_timeout` verifies one actual SDK request attempt for the timeout and each HTTP failure; SDK auto_retry is false.
- [x] **AC 5 — sandbox CLI/report and B2 outcomes**: `test_order_outcomes`, `test_probe_failures_and_duplicate_findings`, `test_final_fill_has_no_remainder_to_cancel`, `test_cancel_reject_is_resolved_by_status`, `test_cli_missing_variables_exit_two`, and `test_cli_fake_transport_reports_and_exit_codes`. They verify accepted_cancelled, rejected, filled, rejected_as_expected, accepted_unexpectedly and skipped, plus transport/auth, missing-order and cancel-confirmation failures. Exit codes 0/1/2, codes, per-call HTTP status/latency, per-check call association/latency, SDK version, field-presence findings, statuses and sanitized JSON reports are exercised offline. No MARKET order is generated by the check.
- [x] **AC 6 — SDK import isolation**: `uv run lint-imports` and architecture mutation tests pass. The SDK contract now also forbids imports from broker.port and broker.sim; the concrete-adapter contract now includes Webull as well as SimBroker.
- [x] **AC 7 — required checks and coverage**: all mandatory commands pass; every existing and new reported module has 100% statement and branch coverage.
- [x] **AC 8 — deterministic fixture backtests**: both runs return the same run ID and recursive diff exits 0.
- [x] **Documentation**: developer setup explains variables, configuration, operator invocation, safe probes, exit codes and report contents. Document 14 contains only the authorized automation note under section 8; its U16 assumptions were not edited.

Additional mapping evidence: `test_conservative_cash`, `test_missing_currencies_and_non_usd_positions`, `test_status_mapping`, `test_optional_price_absence`, `test_required_order_fields_missing`, `test_required_balance_fields`, `test_unused_position_numbers_and_required_place_time_are_validated`, `test_all_costs_and_missing_actuals`, `test_pagination_and_fill_deduplication`, `test_pagination_cap_and_history_time_limits`, `test_query_limiter_threshold`, `test_paging_paces_queries_without_repeating_transport`, `test_non_json_http_responses_preserve_status`, and `test_documented_rate_limit_code`.

## SDK logging configuration
At broker construction, every logger below is set to `logging.CRITICAL + 1`, `propagate = False`, with a `logging.NullHandler()` replacing its handlers. These names were found in the installed SDK source, including the SDK's custom logger defaults:

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
None. The SDK exposes raw responses, so parsers and hand-written fixtures follow the designer's reference, not inferred response models. The adapter constructs ApiClient and official request objects directly instead of TradeClient, whose constructor makes additional token/configuration calls. It does not initiate 2FA. The token directory is private, temporary and outside the repository, as explicitly required by the task.

The optional sandbox configuration is now part of the Settings schema, so the configuration hash and fixture manifest/run ID differ from earlier tasks. Both runs of the current configuration are byte-identical; no backtest execution or strategy code was changed.

## Open issues and questions
No unresolved implementation issue or designer decision is required for this offline task.

**Operator sandbox verification: NOT_RUN**, as explicitly required. After the operator's merge-ready push, the operator must run `uv run aitrader sandbox check` once on the Mac with their sandbox environment variables and send the generated report to the designer. Offline fixtures prove request construction, guards, mapping and command outcomes; they do not establish actual UAT availability, field shapes, rejection codes, shared-account state, balance/position semantics or fill realism. Actual findings belong in document 14 in a later commit.

**Owner PR approval:** the new Webull adapter is the task's explicitly authorized protected path; owner approval is still required in the PR before merge. Git work remains the operator's responsibility.

GUI verification: NOT_RUN; this task implements a broker adapter and CLI. CLI actions and Expected/Actual exit codes and reports were verified using CliRunner and the fixture command executions above.

Intentionally not performed: real sandbox/API calls, production requests, credential inspection, dependency installation/downloads, SDK token creation or 2FA, SB-4 event streaming, SB-5 market data, LIVE/PAPER routing changes, hard-cap/risk/gate edits, production configuration, external services, Git mutations or a second implementation cycle.
