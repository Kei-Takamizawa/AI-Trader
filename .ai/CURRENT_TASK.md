# CURRENT_TASK — TASK-016: Windows operator PC

Designer: Claude, 2026-10-26. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-015.md`.

Owner decision 2026-10-26: the operator PC moves from the Mac to Windows 11
(`D:\my-workspace\AI-Trader`, Windows PowerShell 5.1, uv 0.13.5, CPython 3.14.9). PAPER must run
there from its first session (2026-10-26 close). On Windows today ruff, mypy and lint-imports
pass, but about 70 tests fail and `aitrader paper run` crashes. The engine is still designed to
run on Linux later (document 04); this task makes the existing behaviour correct on Windows too,
without changing any behaviour on Linux or macOS.

No protected path is touched. Do not edit `src/aitrader/broker/webull/`, `src/aitrader/risk/`,
`src/aitrader/execution/` or `config/`.

## Failures seen on Windows (operator run, 2026-10-26)

1. `src/aitrader/storage/instance.py`, `SingleInstanceLock`: `msvcrt.locking` locks and unlocks
   the byte range starting at the **current file position**. `acquire` writes the PID after
   locking, so `release` tries to unlock a different range and raises
   `PermissionError: [Errno 13]`. Every `run_paper` call fails in `__exit__`
   (`tests/integration/test_paper.py`, almost all tests; `tests/unit/storage/test_storage.py::
   test_a_second_engine_cannot_take_the_file_lock`).
2. `src/aitrader/app/paper_reports.py`, `atomic_write`: when `fsync` fails, the temporary file is
   unlinked while still open; Windows refuses (`WinError 32`), so the original error is masked
   and the temporary file is left behind (`test_failed_atomic_write_leaves_old_complete_file
   [flush]`).
3. `tests/architecture/test_import_contracts.py::test_forbidden_import_breaks_a_contract`: the
   `lint-imports` subprocess output is decoded with the Windows locale code page (cp932 here),
   `UnicodeDecodeError` in the reader thread, `result.stdout` is `None`.
4. `tests/unit/broker/test_webull.py::test_real_sdk_client_override_pin_no_retries_and_cleanup`
   and `tests/unit/broker/test_webull_tokens.py::test_sdk_token_enabled_normal_or_disabled`
   assert `st_mode & 0o777 == 0o700` on the token directory. Windows does not implement POSIX
   mode bits (`os.chmod` only toggles read-only); the directory is under the user's own
   `%TEMP%`, protected by the profile ACL.

Run the full suite on Windows yourself first; if a failure appears that is not one of these four
kinds, fix it the same way (smallest change, no behaviour change on POSIX) and list it in the
report. If a fix would need a protected path, stop with BLOCKED.

## Scope

### A. Single-instance lock (product code)

- Lock and unlock the same byte range regardless of the file position: seek to offset 0 before
  `msvcrt.locking` in both `_lock` and `_unlock` (and restore nothing else). Keep the POSIX
  `fcntl.flock` path unchanged.
- Remove the `# pragma: no cover` claim that Windows is only for dev PCs; update the module
  docstring to say the file lock works on POSIX and Windows. Coverage is measured per platform:
  keep the Windows branch excluded from coverage on Linux CI the same way it is today (a
  platform-specific pragma is fine), and make sure it is exercised by the tests on Windows.
- Tests: acquire, write PID, release, acquire again in the same process; a second process (or a
  second handle) is refused while held and succeeds after release. Must pass on Windows and Linux.

### B. Atomic write (product code)

- `atomic_write` closes the temporary file before unlinking it on failure, so the original
  exception propagates and no temporary file is left, on every platform. Same success behaviour.
- The existing test covers it; it must pass on Windows.

### C. Test portability (tests only)

- Subprocesses in tests that read text output use `encoding="utf-8"` (and set
  `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` in the child env where the child is Python).
- The two Webull token-directory tests check `0o700` only on POSIX; on Windows they check that
  the directory exists inside the expected temporary root during the run and is removed after.
  Do not change the adapter.
- Any other test that assumes POSIX paths, `/` separators, line endings or file modes gets the
  smallest portable fix. Never skip a test on Windows unless the behaviour itself is POSIX-only,
  and then say why in the test and in the report.

### D. CI on Windows

- `.github/workflows/ci.yml`: add a second job `windows` on `windows-latest` with the same uv
  version and Python, running `uv sync --frozen`, `uv run pytest` and
  `uv run aitrader live` (expect exit 3, written for PowerShell or `shell: bash`). The coverage
  gate stays on the Linux job only. Do not change the Linux job.

### E. Docs

- `docs/dev/setup.md`: a short "Windows operator PC" section: uv via `winget install
  astral-sh.uv`; `UV_LINK_MODE=copy` when the uv cache and the repository are on different
  drives; credentials as Windows **user** environment variables (no values, no commands that
  echo values); commands are the same with `uv run`; the fixture backtest comparison uses
  `Compare-Object`/`fc.exe` or `git diff --no-index` instead of `diff -r` in PowerShell.

## Tests

1. The full suite passes on Windows (operator PC) and on Linux (CI).
2. New lock tests as in A.
3. `atomic_write` failure test passes on Windows (no temporary file left, original error raised).

## Out of scope

- Running PAPER on real data, scheduling (Task Scheduler), notifications.
- Any change to strategies, risk, execution, broker adapters or configuration.

## Acceptance criteria

1. On the Windows operator PC: all `AGENTS.md` checks green; `uv run pytest` 0 failures.
2. Coverage on Linux CI stays 100% where it is 100% today.
3. The fixture backtest double run is byte-identical.
4. `git diff --stat` in LAST_REPORT shows no protected path and no change under
   `src/aitrader/broker/`, `src/aitrader/risk/`, `src/aitrader/execution/` or `config/`.
