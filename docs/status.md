# Project status

Last updated 2026-10-24 by Claude (designer). Read this first in a new session, then
`CLAUDE.md` and `docs/design/README.md`.

## Where the project stands

Phase P1 (offline core) is complete; phase P2 (real data and research) has started. Everything
runs offline on synthetic fixtures; no external service is called by tests or CI. LIVE is disabled
in this build.

| Task | What | State |
|---|---|---|
| TASK-001 | Project skeleton, domain model, configuration with hard caps, clock, CLI, CI | done |
| TASK-002 | Fee model, position sizing, risk engine (all pre-trade rules), halt state machine | done |
| TASK-003 | Double-entry ledger: cash per currency, T+1 settlement, fees, FX, dividends, operating costs | done |
| TASK-004 | Order state machine, order gate (HC-1 to HC-11, DUP-1), order manager, error monitor, SimBroker | done |
| TASK-005 | Exchange calendar, bar data and look-ahead guard, indicators, strategy S2, backtest loop, metrics, run manifest, `aitrader backtest` | done |
| TASK-006 | Read-only Alpaca client, universe U0 (`config/universe.yaml`), `aitrader data fetch`, data checks | code done; **real download pending** (operator PC) |
| TASK-009 | SQLite storage: orders, journal, halts, decisions, runs, single-instance lock and lease | done |
| TASK-010 | Reconciliation of the engine's books against the broker | done |
| TASK-007a | Point-in-time adjusted signals, raw fills, dividends as cash | **next; first Codex task** (`.ai/CURRENT_TASK.md`) |
| TASK-007b | Strategy S1 and the walk-forward runner | after 007a |
| TASK-007c | Research report on real data (operator runs it) | after 007b and the real download |
| TASK-008 | Webull adapter on the official SDK, contract tests, sandbox runs | later (phase P3b) |

All of the above up to TASK-010 is on `main`: pull request
[Kei-Takamizawa/AI-Trader#1](https://github.com/Kei-Takamizawa/AI-Trader/pull/1) was merged on
2026-10-24 after the owner approved it, including its protected paths (approval recorded in the PR).

The operator PC is the owner's Mac (repository in `~/Documents/AI-Trader`; uv 0.12.1 and
codex-cli 0.164.0 installed, Codex signed in, the flags in `CLAUDE.md` present). The first
`uv sync` and the checks there are still pending.

## Roles (since 2026-10-24)

The Codex loop is active (`CLAUDE.md`, document 06): Claude in the cloud designs, reviews and
merges; Claude Code on the owner's PC is the operator and does all Git work; Codex implements and
tests. Claude implemented TASK-002 to TASK-006, TASK-009 and TASK-010 itself before the switch (owner instruction
2026-10-23).

## What the owner needs to do

1. **Alpaca data key, on the operator PC only:** create an API key for the Alpaca **paper**
   account and set it as the user environment variables `AITRADER_DATA_API_KEY` and
   `AITRADER_DATA_API_SECRET` (for example through the operating system's environment-variable
   settings, so it does not end up in a shell history). Never paste it into a chat, a file, the
   repository, a cloud environment or a Codex or Claude configuration.
2. **Universe U0:** look at `config/universe.yaml` (23 ETFs) before the first research run;
   document 12 requires the universe to be fixed before any backtest on real data.

## Next steps

1. **Operator, finish the one-time setup on the Mac:** `uv sync` and the checks from
   `docs/dev/setup.md` must pass before the first Codex run.
2. **Cycle 1 (TASK-007a):** cycle branch `claude/project-thread-whk5dv`, created from `main`
   (the spec is in `.ai/CURRENT_TASK.md`); the operator fetches it, runs Codex
   with the command in `CLAUDE.md`, checks `.ai/LAST_REPORT.md` and the checks, commits only the
   listed files, pushes and opens a draft PR; the designer reviews and merges under the merge
   rule.
3. **Real download (TASK-006), any time after step 1 and the key:** the operator runs
   `uv run aitrader data fetch --end <last closed session>` and sends the designer a summary of
   `data/bars/fetch_report.json` (rows, missing sessions, large moves, corporate actions; no data
   is committed). Splits found there decide whether TASK-007a's fail-closed rule is enough.
4. TASK-007b, then TASK-007c (research report and go/no-go per strategy, phase P2 exit).

## Open items

- **Secret scanning in CI** is required by document 06 §5 but not in place yet. The tool must be
  chosen (free options first; anything paid needs the owner's approval). Until then the reviewer
  checks every diff for credentials.
- **Corporate actions:** TASK-007a estimates dividends from the raw and adjusted series and
  books them on the ex-date. Exact declared amounts and pay dates need a corporate-actions data
  source, to be verified by the designer before a task is written.
- **Withholding rates** in `config/backtest.yaml` (after TASK-007a) are assumptions until
  confirmed with Webull's tax documents (WB-8, document 14).
