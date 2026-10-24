# CURRENT_TASK — TASK-013: PAPER stage 1, a local daily runner verified by replay

Designer: Claude (cloud), 2026-10-24. Implementer and tester: Codex. Read `AGENTS.md` first.
The previous task's spec is archived in `docs/tasks/TASK-012.md`.

Design sources:
- document 04 §11 (new, **read it first**): PAPER stage 1 and why it replays daily bars;
- document 04 §1–§2 (environments), document 13 §4.1 (status bundle and daily report);
- document 07 (P3 goals), `docs/research/2026-10-24-s1-v1.1-results.md` (S1 1.0.0 stays the
  reference strategy);
- `src/aitrader/backtest/engine.py`, `src/aitrader/config/settings.py`, `config/paper.yaml`,
  `src/aitrader/storage/`, `src/aitrader/cli/main.py`.

No protected path is touched: `risk/`, `hard_caps.py`, `execution/gate*`, `broker/webull/` and
`config/live*` stay unchanged. No network access, no scheduler, no cloud resources.

## Goal

`aitrader paper run` advances our own virtual portfolio (PAPER, `SimBroker`, S1 1.0.0) to the
latest completed session in the local data, using exactly the backtest code path. Every run
replays the book from the PAPER start date and checks that the replay reproduces every session
already recorded. A mismatch halts the book. Missed days (the Mac was asleep) are caught up on the
next run. Re-running for the same data changes nothing.

## Scope

### A. Settings

- New `SimulationSettings` model with the fields the simulator needs: `initial_capital_usd`,
  `fx_jpy_per_usd`, `half_spread_bps`, `max_participation_pct`, `dividend_us_withholding_pct`,
  `dividend_jp_withholding_pct` (same types, defaults and validation as today).
- `BacktestSettings` keeps its flat YAML keys and gains a method or property that returns its
  `SimulationSettings`. `PaperSettings` becomes:
  - `start: date` (the first session the PAPER book may trade);
  - the same six simulation fields, flat, with the same defaults;
  - `virtual_capital_jpy` is removed (the book is in USD like the backtest; the JPY figure is
    `initial_capital_usd × fx_jpy_per_usd` in reports).
- `config/paper.yaml`: `paper.start: 2026-10-26`, `initial_capital_usd: 323`, `fx_jpy_per_usd: 155`,
  and the other simulation values equal to `config/backtest.yaml`. Keep the comments accurate.
- `run_backtest` gets an optional `simulation: SimulationSettings | None = None`; `None` means the
  backtest settings, as today.
- **Backtest outputs, the manifest and the run ID must be byte-identical to before** (fixture
  double run against the pre-task output; say so in LAST_REPORT). If `config_hash` would change
  for BACKTEST, the refactor is wrong.

### B. Paper runner (`aitrader.app.paper` or a new `aitrader.paper` package; respect the
import-linter contracts and add one for the new package if it needs a rule)

`run_paper(settings, source, adjusted, state_dir, through, clock, git_commit)`:

1. Requires `settings.mode is Mode.PAPER` and `broker_environment is SIM`.
2. `data_end` = the last SPY session in `source`. `through` defaults to `data_end` and may not be
   later. If `through < paper.start`, nothing is recorded and the status says the book has not
   started.
3. Replays `run_backtest(settings, S1 1.0.0 default params, source, adjusted=…, start=paper.start,
   end=through, simulation=paper simulation settings)`. Same strategy, risk, execution and
   SimBroker code as BACKTEST; no new trading logic.
4. Builds one **session snapshot** per replayed session: session date, equity, cash (settled and
   unsettled if available), positions (symbol, quantity, average cost, close, market value),
   fills of that session (symbol, side, quantity, price, fees), the intents produced at that
   close for the next session (symbol, side, purpose, target, stop, reason), risk rejections if
   the cycle exposes them, and a `snapshot_hash` (sha256 of the canonical JSON of the snapshot,
   sorted keys, Decimal as strings).
5. State lives in `state_dir/paper.db` (SQLite through `aitrader.storage.Database`; add the new
   tables as a new schema step, never by editing an existing step):
   - `paper_book`: one row holding `start`, `strategy_id`, `strategy_version`, `params_hash`,
     `simulation` (canonical JSON) and the `risk` limits hash, written on the first run;
   - `paper_sessions`: `session` (primary key), `snapshot_json`, `snapshot_hash`, `recorded_at`,
     `git_commit`, `data_snapshot_hash`.
6. On every run, before writing:
   - if the book row exists and any of its fields differs from the current configuration, refuse
     (new exit code `EXIT_PAPER_MISMATCH = 6`) with a message that a changed book needs a new
     state directory;
   - for every recorded session ≤ `through`, the replay's `snapshot_hash` must equal the recorded
     one. On the first difference: write nothing new, set the status to `HALTED` with the session
     and the differing top-level fields, and exit 6;
   - recorded sessions later than `through` are allowed (an older `--through`) and are left alone.
7. New sessions (after the last recorded one, ≤ `through`) are inserted in one transaction.
   A second run with the same data inserts nothing and exits 0.
8. A `CorporateActionError` or `DataError` from the replay sets `HALTED` with the reason, records
   nothing, and exits with the existing data-error code.
9. One run at a time: take `SingleInstanceLock` on the state directory; a second concurrent run
   exits with a clear message (existing usage-error code).
10. The wall clock is read only through the injected `aitrader.clock` clock (`recorded_at`, data
    staleness). No `datetime.now()` elsewhere.

### C. Status bundle and daily report (document 13 §4.1, files only)

Written under `state_dir` after every run, also when nothing new was recorded:

- `status/latest.json`: `state` (`NOT_STARTED`, `RUNNING` or `HALTED`) and reason; `as_of`
  (clock); `start`; `last_session`; `sessions_recorded`; `data_end`; `data_stale` (true when
  `data_end` is earlier than the last completed session before `as_of` by the exchange calendar);
  equity in USD and JPY; return since start; max drawdown since start; open positions; the intents
  waiting for the next session; B0 (SPY buy and hold from `start`) return since start; git commit.
  No account IDs, no secrets.
- `reports/daily/YYYY-MM-DD.json` and `.md` for each **newly recorded** session: equity and day
  P&L, fills with fees, positions, next-session intents with reasons, rejections, and the
  data-quality findings of that session if available. The Markdown is short and readable by a
  non-engineer. Existing report files are never rewritten.
- Writes are atomic (temporary file and rename).

### D. CLI

`aitrader paper run [--config config/paper.yaml] [--data-dir data/bars] [--state-dir data/paper]
[--through YYYY-MM-DD] [--git-commit SHA]`. Exit codes: 0 success (including nothing new),
2 configuration, 4 data, 5 usage, 6 paper mismatch. It prints one summary line (state, sessions
added, last session, equity). The existing `aitrader live` behaviour is unchanged.

### E. Docs

- `docs/dev/setup.md`: a short "PAPER stage 1" section: daily routine for the operator
  (`aitrader data fetch`, then `aitrader paper run`), what the status and reports mean, what to do
  on exit 6 (stop, report, never delete the state), and that catching up after missed days is
  automatic.

## Tests (offline, fixtures only)

1. First run over a fixture period records every session from `start` to `through`; the
   snapshots equal the corresponding sessions of a plain `run_backtest` over the same period
   (parity).
2. Re-running with the same data inserts nothing, rewrites no daily report, exits 0.
3. Running with a later `through` adds only the new sessions and verifies the earlier ones.
4. A tampered `snapshot_hash` (or snapshot) in the database → `HALTED`, exit 6, nothing written.
5. Changed strategy params, start, simulation value or risk limits → exit 6 with the message.
6. `through` before `start` → `NOT_STARTED`, nothing recorded.
7. `through` after `data_end` → usage error.
8. A held lock → refused.
9. A corporate action on a held position → `HALTED`, data-error exit, nothing recorded.
10. Status fields, `data_stale` true and false with an injected clock, atomic writes, and report
    content for one session with fills.
11. BACKTEST settings: `simulation()` equals today's values; the fixture double run is
    byte-identical to the pre-task output.
12. Building the PAPER runner never imports `aitrader.broker.webull` (document 04 §4).

## Out of scope

- Live quotes, intraday fills, `WallClock` scheduling, launchd/cron/cloud scheduling (each needs
  the owner's approval when proposed), notifications, kill switch, heartbeat, Google Cloud.
- Any change to strategy logic, risk limits or the execution path.

## Acceptance criteria

1. All tests above, plus every existing test, pass; coverage stays 100% for every module that is
   100% today and never below 90%.
2. The fixture backtest double run is byte-identical to the pre-task output (manifest and run ID
   included).
3. A fixture run of `aitrader paper run` with a temporary config whose `paper.start` falls inside
   the fixture data records sessions, and a second run adds none (commands and outputs summarised
   in LAST_REPORT; temporary files outside the repository or removed).
4. All checks in `AGENTS.md` are green.
5. No change to protected paths (`git diff --stat` in LAST_REPORT).
