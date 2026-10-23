# 07. Development Roadmap

Each phase ends with an exit review by Claude and a go/no-go by the owner. Durations are rough estimates [A]; they depend on Codex cycle speed and on how much paper time the evidence needs. Paper-trading durations are minimums, not targets.

## Overview

```mermaid
flowchart LR
    p0["P0 Design"] --> p1["P1 Offline core + backtest skeleton"]
    p1 --> p2["P2 Historical data + strategy research"]
    p2 --> p3["P3 PAPER on cloud (sim + broker paper)"]
    p3 --> p4["P4 AI analysis: shadow, then A/B"]
    p3 --> p5["P5 Monitoring interface + Muse check"]
    p4 --> p6["P6 Live preparation"]
    p5 --> p6
    p6 --> p7["P7 LIVE_MICRO (only if approved)"]
    p7 --> p8["P8 Staged scaling + research loop"]
```

| Phase | Goal | Main deliverables | Owner approvals needed | Exit criteria | Estimate |
|---|---|---|---|---|---|
| **P0** Design | Agree on the design | This package | Q1 | Owner approves (with changes if any) | now |
| **P1** Offline core | Working skeleton with no external services | Project setup, domain model, config with hard caps, risk engine, ledger, order manager, `SimBroker`, backtest loop on fixture data, metrics, CLI, CI | none | CI green; fixture backtest reproducible (same inputs → same outputs, same manifest hash); every risk rule tested; import rules enforced | 2–4 weeks |
| **P2** Data + research | Real historical data and baseline strategies | Data ingestion + quality gate + Parquet store; S1/S2 baseline strategies; walk-forward runner; locked hold-out; research report | Q2 (Alpaca paper-only account, free) | Report with all metrics in document 12, including costs and benchmark; hold-out not touched; documented go/no-go per strategy | 2–4 weeks |
| **P3** PAPER | Unattended operation with live data | Scheduler, live data adapter, PAPER_SIM, Alpaca paper adapter, reconciliation, kill switch, heartbeat + watchdog, notifications, daily report, operating-cost ledger, cloud deployment | Q3 (cloud project), Q4 (channels) | ≥ 20 consecutive trading days without unhandled incidents; reconciliation clean every day; fault drills (kill process, cut network, stale data, kill switch) passed | 4–8 weeks |
| **P4** AI | Measure whether AI adds value | News/filings/macro ingestion; AI analysis with structured outputs; replay store; budget guard; **shadow mode** (recorded, not used) ≥ 4 weeks; then two parallel paper books: baseline vs. AI overlay | Q6 (model, budget) | A/B report with statistical test (document 12); AI cost vs. benefit; decision to keep, change or drop the overlay | 3–6 months (runs in parallel with P5/P6) |
| **P5** Monitoring | External read-only access | Status bundle, read-only status API (private), report templates, Muse verification (document 06) | Muse data-sharing decision | Owner can see health and P&L without logging into the server; Muse decision recorded | 2–3 weeks |
| **P6** Live prep | Decide whether live trading is technically and economically possible | Broker verification answers (document 02 §6); owner opens the chosen account; adapter for the broker's paper/test environment; fault tests on that adapter; economic feasibility review; checklist (document 09) | Q7 (broker), account opening | Checklist complete. **If the economic or technical gates fail, the project stays in PAPER** | 2–4 weeks |
| **P7** LIVE_MICRO | Validate real execution at minimum size | LIVE host and secrets, LiveGuard approval file, live limits, go-live runbook | Explicit written LIVE approval | ≥ 2–3 months with no unexplained reconciliation differences; slippage and costs within expectations; no limit breaches | 2–3 months minimum |
| **P8** Scaling + research | Improve safely | Limit increases in steps (each approved); research sandbox; strategy promotion process (document 11) | Each limit increase | Ongoing | ongoing |

Earliest realistic LIVE_MICRO start: roughly 5–8 months after P1 starts [A], because AI evaluation and paper stability need calendar time. Starting LIVE earlier without AI is possible (rule-based only) if the owner prefers; the AI A/B can then continue in paper.

## Codex task backlog (P1 and early P2)

| Task | Title | Depends on |
|---|---|---|
| TASK-001 | Project skeleton, domain model, configuration with hard caps, CI | — |
| TASK-002 | Risk engine (all R-*, DQ-*, CB-* rules that work on fixture data) with unit and property tests | 001 |
| TASK-003 | Ledger (cash by currency, settled cash, fees, operating costs) with property tests | 001 |
| TASK-004 | Order manager state machine, order gate, `SimBroker`, fault tests | 002, 003 |
| TASK-005 | Backtest event loop, fill and cost models, metrics, run manifest, CLI `backtest` | 004 |
| TASK-006 | Market data ingestion (Alpaca historical), quality gate, Parquet store | 005, Q2 |
| TASK-007 | Baseline strategies S1/S2 + walk-forward runner + research report | 006 |

TASK-001 is specified in detail in document 10. Later tasks are specified one at a time after the previous one is merged.
