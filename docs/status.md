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
| TASK-006 | Read-only Alpaca client, universe U0 (`config/universe.yaml`), `aitrader data fetch`, data checks | done; first real download 2026-10-24 (see below) |
| TASK-009 | SQLite storage: orders, journal, halts, decisions, runs, single-instance lock and lease | done |
| TASK-010 | Reconciliation of the engine's books against the broker | done |
| TASK-007a | Point-in-time adjusted signals, raw fills, dividends as cash, exact split ratios | done (Codex cycle 1, PR #2) |
| TASK-007b | Strategy S1, walk-forward runner, minor vendor adjustments | done (Codex cycle 2, PR #3) |
| TASK-007c | Research report on real data | done: `docs/research/2026-10-24-p2-walkforward.md`; S1 and S2 no-go for LIVE |
| TASK-011 | Free-plan daily data fetch, commit lookup, SQLite warning | done (Codex cycle 3, PR #5) |
| TASK-008 | Webull adapter (sandbox only) on the official SDK, contract tests, sandbox check | **in progress** (Codex cycle 4; protected path, owner approval needed in the PR) |

All of the above up to TASK-010 is on `main`: pull request
[Kei-Takamizawa/AI-Trader#1](https://github.com/Kei-Takamizawa/AI-Trader/pull/1) was merged on
2026-10-24 after the owner approved it, including its protected paths (approval recorded in the PR).

TASK-007a was the first Codex cycle (pull request #2, one review round: exact split ratios and a
CI-safe temporary directory in the property test).

The operator PC is the owner's Mac (repository in `~/Documents/AI-Trader`; uv 0.12.1 and
codex-cli 0.164.0, Codex signed in; all checks pass there). The Alpaca key is set there as
user environment variables read from the macOS Keychain.

### First real download (2026-10-24, `--end 2026-10-16`)

23 symbols, 2016-01-04 to 2026-10-16 (XLC from 2018-06-19), 2,094 to 2,713 rows per symbol,
no missing sessions (DQ-2), no large moves (DQ-3). Corporate actions: 1,180 distributions,
7 splits (BIL 2017-11-30, IAU 2021-05-24, XLB/XLE/XLK/XLU/XLY 2025-12-05), 16 unexplained
factors between 1.0005 and 1.0009 (XLE 11, XLU 4, XLF 1). Snapshot hashes: raw
`f9469b49…5a5815`, adjusted `8985aaec…5a5c52`. `--end 2026-10-23` was refused with 403
"subscription does not permit querying recent SIP data" (the client always sends `feed=sip`
and a date-only `end`).

## Roles (since 2026-10-24)

The Codex loop is active (`CLAUDE.md`, document 06): Claude in the cloud designs, reviews and
merges; Claude Code on the owner's PC is the operator and does all Git work; Codex implements and
tests. Claude implemented TASK-002 to TASK-006, TASK-009 and TASK-010 itself before the switch (owner instruction
2026-10-23).

## What the owner needs to do

1. **Universe U0:** look at `config/universe.yaml` (23 ETFs) before the first research run;
   document 12 requires the universe to be fixed before any backtest on real data.

## Next steps

Owner decision 2026-10-24 (research report, section 7): build the paper-trading path with S1 1.0.0
as the reference strategy, and research S1 1.1 in parallel.

1. Done (TASK-011, PR #5): `data fetch` now gets the latest closed session on the free plan
   (verified 2026-10-24: `--end 2026-10-23`, 23 symbols, no missing sessions; raw
   `6f0b2780…cd8eac`, adjusted `965e8cfa…7a0682`).
2. **Cycle 4 (TASK-008, P3b, in progress):** Webull adapter for the sandbox only, on
   `webull-openapi-python-sdk==3.0.4`, broker contract tests, and `aitrader sandbox check`
   (SB-1, SB-2 subset, SB-3, SB-6) run by the operator. Spec in `.ai/CURRENT_TASK.md`.
3. **S1 1.1 research plan:** changes declared before any run, each counted as a variant.
4. **P3 PAPER:** scheduler, live data adapter, virtual portfolio, reconciliation, kill switch,
   heartbeat, notifications, daily report. Cloud deployment needs the owner's approval to create
   the Google Cloud project `aitrader-paper` (asked at that time).

## Open items

- `tests/unit/data/test_alpaca.py::test_urllib_transport_against_a_local_server` leaves an
  `HTTPError` unclosed (a `ResourceWarning` under `-W error`); harmless, fix with the next data task.

- **Secret scanning in CI** is required by document 06 §5 but not in place yet. The tool must be
  chosen (free options first; anything paid needs the owner's approval). Until then the reviewer
  checks every diff for credentials.
- **Corporate actions:** TASK-007a estimates dividends from the raw and adjusted series and
  books them on the ex-date. Exact declared amounts and pay dates need a corporate-actions data
  source, to be verified by the designer before a task is written.
- **Withholding rates** in `config/backtest.yaml` (after TASK-007a) are assumptions until
  confirmed with Webull's tax documents (WB-8, document 14).
