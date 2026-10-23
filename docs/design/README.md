# AI-Trader Design Package (v0.1, draft for owner review)

- **Status:** Draft. Nothing in this package has been implemented yet.
- **Date of research:** 2026-10-23. Broker, data and pricing facts change often; every fact is tagged with how it was verified (see legend).
- **Scope:** Design only. No code, no external sign-ups, no billing, no secrets, no cloud deployment, no real-money trading.

## Reading order

| # | Document | Deliverable |
|---|----------|-------------|
| 1 | [01-system-architecture.md](01-system-architecture.md) | System architecture diagram |
| 2 | [02-tech-stack-and-broker-comparison.md](02-tech-stack-and-broker-comparison.md) | Tech stack and broker/data API comparison |
| 3 | [03-module-design.md](03-module-design.md) | Python module design |
| 4 | [04-mode-switching.md](04-mode-switching.md) | BACKTEST / PAPER / LIVE switching design |
| 5 | [05-risk-management-spec.md](05-risk-management-spec.md) | Risk management specification |
| 6 | [06-roles-claude-codex-muse.md](06-roles-claude-codex-muse.md) | Roles of Claude, Codex and Muse |
| 7 | [07-roadmap.md](07-roadmap.md) | Phased development roadmap |
| 8 | [08-cost-estimate.md](08-cost-estimate.md) | Initial and monthly cost estimate |
| 9 | [09-live-readiness-checklist.md](09-live-readiness-checklist.md) | Checklist before moving to real money |
| 10 | [10-first-codex-task.md](10-first-codex-task.md) | First development task for Codex (draft) |
| 11 | [11-strategy-and-ai-analysis.md](11-strategy-and-ai-analysis.md) | Strategy research and AI analysis design |
| 12 | [12-backtest-and-evaluation.md](12-backtest-and-evaluation.md) | Backtest and evaluation method |
| 13 | [13-operations-and-monitoring.md](13-operations-and-monitoring.md) | Unattended operation, monitoring, Muse interface |

## Verification legend

| Tag | Meaning |
|-----|---------|
| **[V]** | Verified on an official page of the provider on 2026-10-23 (URL in the document). |
| **[S]** | Secondary source only (press coverage, comparison site, third-party listing). Treat as a lead, not a fact. |
| **[U]** | Unverified / not found. Must be confirmed before any decision depends on it. |
| **[A]** | Assumption made for planning (for example an FX rate). Replace with measured values later. |

## Key design decisions

| ID | Decision | Why |
|----|----------|-----|
| D-01 | Python 3.12 modular monolith (one trading process) plus a separate watchdog. | Small scale; fewer moving parts means fewer failure modes. Independence is added only where it buys safety (watchdog, order gate). |
| D-02 | One strategy, risk and execution code path for BACKTEST, PAPER and LIVE. Only the clock, data feed, broker adapter and storage namespace change. | Removes the main source of "worked in paper, broke in live" bugs. |
| D-03 | LIVE is disabled by default and guarded by seven independent gates (LiveGuard). No live credentials exist until the owner approves. | Requirement section 4. |
| D-04 | AI is advisory only. It may veto or scale down an order intent produced by rules; it can never create an order or enlarge one. | AI cannot place unlimited orders (requirement section 6). |
| D-05 | The risk engine, order gate, execution and broker packages are forbidden from importing the AI package (checked in CI). | "Risk management independent from AI" (requirement section 7). |
| D-06 | Start with daily-bar, long-only, cash-only, low-frequency ETF strategies. No leverage, no shorting, no options. | Matches the small-capital, low-frequency live plan and keeps costs low. |
| D-07 | PAPER uses an internal simulator first, then a broker paper account. Live-broker shortlist (pending verification): Webull Securities Japan, moomoo Securities Japan, Interactive Brokers Securities Japan. Alpaca is used for paper/data only. | See document 02 for the evidence and the open questions. |
| D-08 | Cloud VM in a US region (Google Cloud Compute Engine `us-east1` proposed), managed by systemd. | Runs while the owner's Mac sleeps; close to US markets; free tier available for the smallest VM. |
| D-09 | SQLite (WAL mode) for state, JSON Lines structured logs, Parquet for market data. | Single-node, file-based, easy to back up and to inspect. |
| D-10 | Economic gate: if small-capital trading cannot pass the cost and risk ratios in document 05, the system stays in PAPER. | Requirement: do not force trades under the ¥100,000 cap. |

## Decisions the owner needs to make

| ID | Question | Recommendation |
|----|----------|----------------|
| Q1 | Approve this design (or list changes). | — |
| Q2 | Approve creating a free Alpaca paper-only account (email sign-up) for market data and broker-side paper trading in Phase 2–3. | Approve when Phase 2 starts. Phase 1 needs no external account. |
| Q3 | Approve creating a Google Cloud project (needs a billing account even for the free tier) in Phase 3. | Approve when Phase 3 starts. |
| Q4 | Notification channels for critical alerts (at least two independent channels). | Email plus one push channel (ntfy, Discord or Telegram). |
| Q5 | After the 5% monthly loss halt: resume automatically next month, or only after manual re-enable? | Manual re-enable. |
| Q6 | AI model and monthly AI budget cap. | Default model `claude-opus-5-5`; hard budget cap USD 10/month during PAPER. Cheaper models are listed in document 08 for your decision. |
| Q7 | Which live broker account to open (Phase 6 only). | Decide after the verification list in document 02 is completed. |

## Change log

| Version | Date | Change |
|---------|------|--------|
| 0.1 | 2026-10-23 | First draft for owner review. |
