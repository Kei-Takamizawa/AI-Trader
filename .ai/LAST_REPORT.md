# LAST_REPORT — TASK-011

Status: DONE

## Summary
Implemented explicit UTC bounds for historical daily bars and CLI validation that the requested end session closed at least 16 minutes before the injected CLI clock. Both backtest commands now share PATH-based Git lookup, with an explicit commit taking priority and command-specific failure handling. Fixed the SQLite connection leak when database initialization rejects a newer schema, and made storage resource warnings fail tests. Updated developer setup instructions.

Execution: operator Mac, repository-local terminal; branch `claude/project-thread-whk5dv`, starting HEAD `e26edc6f5b423ae58a450cddd51aa28071e515f4`. The initial working tree was clean. `.ai/CURRENT_TASK.md` already contained the complete TASK-011 instructions and was preserved unchanged.

## Files changed
- `.ai/LAST_REPORT.md`
- `docs/dev/setup.md`
- `src/aitrader/cli/main.py`
- `src/aitrader/data/alpaca.py`
- `src/aitrader/data/ingest.py`
- `src/aitrader/storage/database.py`
- `tests/integration/test_commit_lookup.py` (added)
- `tests/unit/data/test_alpaca.py`
- `tests/unit/data/test_ingest.py`
- `tests/unit/storage/test_storage.py`

## Commands run
All uv commands used `UV_OFFLINE=1 UV_CACHE_DIR=.uv-cache`; no dependencies were added or downloaded.

- Initial `git status --short`, `git branch --show-current`, `git rev-parse HEAD`: PASS; clean worktree and branch/HEAD recorded above. Final `git diff --check`: PASS.
- Baseline `uv run pytest --cov=aitrader`: PASS, 1006 passed in 18.51s; all modules 100%, with one SQLite ResourceWarning.
- Baseline `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir .task-011-baseline --start 2023-01-03 --end 2024-12-31`: PASS; captured before implementation.
- `uv run ruff format`: PASS, three test files formatted; final run unchanged.
- `uv run ruff check`: PASS, all checks passed.
- `uv run ruff format --check`: PASS, 144 files already formatted.
- `uv run mypy`: PASS, no issues in 143 source files.
- `uv run lint-imports`: PASS, 9 contracts kept, 0 broken.
- `uv run pytest --cov=aitrader`: PASS, 1033 passed in 27.43s, no warnings. All reported modules retain 100% statement and branch coverage (4096 statements, 994 branches).
- `uv run coverage report --include='src/aitrader/*' --fail-under=90`: PASS, 100% total and every reported module 100%.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-a --start 2023-01-03 --end 2024-12-31`: PASS, run ID `bt-42e8b77cb9e17640`.
- `uv run aitrader backtest --data-dir tests/fixtures/bars --out-dir bt-b --start 2023-01-03 --end 2024-12-31`: PASS, same run ID.
- `diff -r bt-a bt-b`: PASS, all output files byte-identical. Generated comparison directories removed after verification.
- `cmp .task-011-baseline/equity.csv bt-a/equity.csv` and `cmp .task-011-baseline/metrics.json bt-a/metrics.json`: PASS, both unchanged from baseline.
- SHA-256 of equity, before and after: `1ce0a8f6a09cafd3c2e0dcf3be8e1e2919d304af941b8e4eee3bae2cbef39363`.
- SHA-256 of metrics, before and after: `d4592f8d1aac44388280c685bd1470b9245372f2dda3d95c05658019a83d09ca`.

Intermediate results: the first focused run had 78 passed and one failed because updating the new required timestamp argument accidentally added a timestamp to an expected session-date list; the expectation was corrected to the original two dates. Initial lint found the dynamic subprocess executable and an intentionally naive datetime in an error test; the latter now derives from a UTC datetime. The subprocess call has a narrowly documented S603 annotation: the task explicitly requires a PATH-resolved executable, arguments are fixed and no shell is used. No global lint configuration was changed. A coverage report attempted before the concurrent full test process finished returned “No data to report”; the final report above was rerun after successful completion.

## Acceptance criteria
- [x] A.3 exact query timestamps and offline requests — `test_request_shape_and_headers`, `test_end_timestamp_caps` check explicit midnight start, end-of-day cap, now-minus-16-minutes cap, second precision and cap boundary. New client and CLI tests use FakeTransport, with no external HTTP request. The existing transport test uses only a local loopback server.
- [x] A.2 refusal and exit code 2 with last closed session — `test_cli_end_requires_a_closed_session` covers one microsecond below, exactly at and one microsecond above 16 minutes for regular and early closes, a holiday, weekend, future session, malformed date and out-of-calendar end. Accepted requests propagate the same CLI timestamp to both raw and adjusted fetches; refused requests produce no downloads or output directory.
- [x] B commit lookup for both commands — `test_commit_lookup` covers explicit override, successful PATH lookup, missing Git, subprocess failure and OS error (10 command cases). Both `shutil.which` and subprocess output are patched; explicit overrides call neither. Backtest records `unknown` on failures; walkforward exits 4 and names `--git-commit`.
- [x] C no ResourceWarning; storage warnings become errors — full suite has no warnings. `test_a_newer_schema_is_refused` verifies the rejected initialization closes the actual SQLite connection. Storage-only module markers promote ResourceWarning and PytestUnraisableExceptionWarning to errors, including leaks detected during destruction.
- [x] Fixture double run byte-identical; existing equity and metrics unchanged — recursive diff, binary comparisons and hashes above.
- [x] All mandatory checks green; coverage retained — final full suite and coverage gate above, every reported module 100%.
- [x] `fetch_report.json` format unchanged — existing `test_fetch_universe_writes_both_series_and_a_report` remains passing; report serialization was not modified.
- [x] Developer setup documents the last closed session, 16-minute rule and walkforward explicit commit option.

## Deviations from the task or the design
None. The default backtest manifest now resolves HEAD as required, so its manifest/run ID changes from the old default `unknown`; equity and metrics remain identical.

## Open issues and questions
None requiring a designer decision.

GUI verification: NOT_RUN; this task changes CLI and storage behavior only. CLI behavior was verified through CliRunner and the fixture command runs above.

Operator verification: NOT_RUN as explicitly excluded by the task. The operator still needs to run `uv run aitrader data fetch --end <last closed session>` with their existing credentials to verify actual free-plan service behavior. Offline tests prove request construction and validation, not provider acceptance.

Intentionally not performed: external service requests, credential inspection, dependency installation, design/protected-path changes, live behavior, Git staging, commits, pushes, branch changes or merges. Temporary baseline outputs and test logs were removed after their results were recorded here.
