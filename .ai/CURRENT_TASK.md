# CURRENT_TASK — TASK-011: daily data fetch on the free plan, commit lookup, SQLite warning

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-007b.md`.

Phase P3 (PAPER) needs the latest closed session's bars every trading day. This small task
removes three obstacles found during P2. No new dependency, no network, no credential and no
protected path are needed. Tests stay offline (recorded-format responses, as for TASK-006).

## A. `end` sent as a timestamp (`src/aitrader/data/alpaca.py`, `data/ingest.py`, CLI)

**Finding (2026-10-24):** `aitrader data fetch --end 2026-10-23` was refused with
`403 subscription does not permit querying recent SIP data` at 02:38 UTC on 2026-10-24, about
6.5 hours after that session closed. `--end 2026-10-16` worked. The client sends `end` as a bare
date (`"2026-10-23"`).

**Facts from Alpaca's documentation and forum** (checked 2026-10-24):
- Without a subscription, the `end` of a historical SIP query must be at least 15 minutes old
  (Market Data FAQ).
- Alpaca staff recommend that free-plan users omit `end` or keep it before "now − 15 minutes".

**Not verified:** how Alpaca reads a bare date for `end`. Reading it as the end of that day in
New York time (04:00 UTC on 2026-10-24, still in the future at 02:38 UTC) would explain the
refusal. Do not depend on this guess: send an explicit timestamp instead.

**Required:**

1. `AlpacaDataClient.daily_bars` takes `start: date`, `end: date` and `now: datetime` (UTC) and
   sends:
   - `start` as `<start>T00:00:00Z`;
   - `end` as the earlier of `<end>T23:59:59Z` and `now - 16 minutes`, formatted RFC 3339 with
     a `Z`, seconds only.
   Daily bars are stamped at midnight New York time on their session date, which is 04:00 or
   05:00 UTC, so the session on `end` is included.
2. `data fetch` refuses an `--end` whose session close (from `TradingCalendar`) is less than
   16 minutes before `now`, or which is not a session. It exits with code 2 and names the last
   closed session. `now` comes from the CLI's clock (`aitrader.clock`), never from
   `datetime.now()`.
3. Tests check the exact query strings in both cases (end-of-day cap and the now − 16 minutes
   cap), the refusal at, just before and just after the 16-minute boundary, and a non-session
   `--end`.
4. `fetch_report.json` keeps its format.

The operator then verifies on the Mac with `uv run aitrader data fetch --end <last closed session>`.
That run is not part of this task.

## B. Commit lookup (`src/aitrader/cli/main.py`)

- `walkforward` gains `--git-commit` like `backtest`. Both commands use one helper:
  1. an explicit `--git-commit` wins;
  2. otherwise run `git rev-parse HEAD` with the `git` found on `PATH` (`shutil.which`), not a
     fixed `/usr/bin/git`;
  3. if `git` is missing or fails, `backtest` records `"unknown"` as today, and `walkforward`
     exits with code 4 and a message saying `--git-commit` can be passed.
- Tests cover all three paths for both commands, with `subprocess` and `shutil.which` patched
  (no real git call needed).

## C. SQLite `ResourceWarning`

- Some storage tests emit `ResourceWarning: unclosed database` attributed to
  `src/aitrader/storage/database.py`. Find the cause and fix it in the code if the code leaks
  a connection, or in the tests if only a test does. Do not silence the warning.
- Add `-W error::ResourceWarning` handling only for the storage tests (for example a
  `pytestmark = pytest.mark.filterwarnings("error::ResourceWarning")` in those test modules).
  A future leak then fails a test.

## Docs

- `docs/dev/setup.md`: `data fetch` accepts the last closed session; mention the 16-minute rule
  and `--git-commit` for `walkforward`.

## Out of scope

The IEX feed, a scheduler, the live data adapter for PAPER, Webull, and risk limits.

## Acceptance criteria

1. The query-string tests in A.3 pass, and no `requests` call reaches the network in tests.
2. The refusal tests in A.2 pass, with exit code 2 and the last closed session in the message.
3. The commit-lookup tests in B pass for both commands.
4. The full test run shows no `ResourceWarning`, and the storage tests turn one into an error.
5. The fixture backtest double run from `AGENTS.md` is byte-identical, and its `equity.csv`
   and existing `metrics.json` values are unchanged from before your change.
6. All checks in `AGENTS.md` are green; coverage stays at 100% where it is 100% today and never
   below 90% for any package.
