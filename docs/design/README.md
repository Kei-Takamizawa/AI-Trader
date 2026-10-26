# AI-Trader Design Package (v0.3, draft for owner review)

- **Status:** Draft. Nothing in this package has been implemented yet.
- **Date of research:** 2026-10-23. Broker, data and pricing facts change often; every fact is tagged with how it was verified (see legend).
- **Scope:** Design only. No code, no external sign-ups, no billing, no secrets, no cloud deployment, no real-money trading.
- **Broker direction:** Webull Securities Japan (Webull OpenAPI) is the first-choice live broker (owner decision, v0.2). The broker connection stays a replaceable adapter.

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
| 14 | [14-webull-openapi-research.md](14-webull-openapi-research.md) | Webull OpenAPI: supported and unsupported features, data terms, costs, unattended-operation constraints |
| 15 | [15-cloud-paper-free-tier.md](15-cloud-paper-free-tier.md) | PAPER daily run on Google Cloud at zero cost (approved: Cloud Run job + Cloud Scheduler) |

## Verification legend

| Tag | Meaning |
|-----|---------|
| **[V]** | Verified on an official page of the provider on 2026-10-23 (URL in the document). |
| **[V-old]** | Stated only in the provider's deprecated official documentation. |
| **[S]** | Secondary source only (press coverage, comparison site, third-party listing). Treat as a lead, not a fact. |
| **[U]** | Unverified / not found. Must be confirmed before any decision depends on it. |
| **[A]** | Assumption made for planning (for example an FX rate). Replace with measured values later. |

## Key design decisions

| ID | Decision | Why |
|----|----------|-----|
| D-01 | Python (3.14 preferred, 3.13 fallback) modular monolith (one trading process) plus a separate watchdog. | Small scale; fewer moving parts means fewer failure modes. Independence is added only where it buys safety (watchdog, order gate). |
| D-02 | Three separated environments — **BACKTEST**, **PAPER** (our own virtual portfolio, run continuously) and **LIVE** (Webull production, disabled) — with one strategy, risk and execution code path. The **Webull Sandbox** is used only for API connection and order-flow tests, never as a portfolio. | Removes the main source of "worked in paper, broke in live" bugs; the sandbox is shared and public (document 14). |
| D-03 | LIVE is disabled by default and guarded by seven independent gates (LiveGuard). LIVE needs separate credentials, a separate execution environment and explicit written approval by the owner. No live credentials exist until then. | Requirement section 4 and v0.2 requirements. |
| D-04 | AI is advisory only. It may veto or scale down an order intent produced by rules; it can never create an order or enlarge one. | AI cannot place unlimited orders (requirement section 6). |
| D-05 | The risk engine, order gate, execution and broker packages are forbidden from importing the AI package (checked in CI). | "Risk management independent from AI" (requirement section 7). |
| D-06 | Start with daily-bar, long-only, cash-only, low-frequency ETF strategies. No leverage, no shorting, no options. | Matches the small-capital, low-frequency live plan and keeps costs low. |
| D-07 | Live broker: **Webull Securities Japan** via the official Python SDK (`webull-openapi-python-sdk`, pinned). moomoo JP and IBSJ remain fallback adapters on paper only. Alpaca is considered only as a read-only market data source for BACKTEST/PAPER. | Owner decision; documents 02 and 14. |
| D-08 | Google Cloud Compute Engine in `us-east1`, separate projects for PAPER and LIVE, managed by systemd. The home PC is for development and BACKTEST only. | Runs while the owner's machines sleep or lose power; static IP for Webull's IP allow-list; production keys stay away from machines where AI agents work (owner decision Q3). Deviation for PAPER stage 1: a Cloud Run job instead of a VM (document 15, approved by the owner 2026-10-26). |
| D-09 | SQLite (WAL mode) for state, JSON Lines structured logs, Parquet for market data. | Single-node, file-based, easy to back up and to inspect. |
| D-10 | Economic gate: if small-capital trading cannot pass the cost and risk ratios in document 05, the system stays in PAPER. | Requirement: do not force trades under the ¥100,000 cap. |
| D-11 | Live capital: start at ¥50,000, hard cap ¥100,000. Sizing uses min(actual equity, approved capital cap), so extra deposits never increase risk. | v0.2 requirement. |
| D-12 | Cash account, long only, US stocks/ETFs, regular hours, fractional orders by amount where the instrument is `fractionable`. `SHORT`, margin, options and extended hours are blocked by hard caps. | v0.2 requirement; document 14. |
| D-13 | Every cost is recorded: commission, CAT/FINRA/SEC fees (from Webull's order fee breakdown), FX spread on each JPY⇄USD conversion, ADR and withdrawal fees — separately from operating costs. | v0.2 requirement. |
| D-14 | New entries stop automatically on API authentication problems (token not `NORMAL`, key expiry near/unknown, 401/403, event-stream auth errors), on data delay, and on order errors. | v0.2 requirement; document 05 sections 4, 8 and 16. |
| D-15 | Claude and Codex never receive production order authority: no production Webull keys in any development environment; Webull MCP/CLI/Agent Skills are never connected to a production account; development uses fixtures and the public sandbox only. Only the owner can generate production keys (SMS + trading password). | v0.2 requirement; document 06. |
| D-16 | The Webull SDK is imported only inside `aitrader.broker.webull`. Everything else talks to the broker through the `BrokerPort` interface, so another broker can replace Webull later. | v0.2 requirement; document 03. |
| D-17 | AI analysis runs **outside** the trading system as a Claude Code scheduled task on the owner's subscription (Claude Opus 5.5). The engine publishes a sanitised input bundle to a private git data repository; the task writes a schema-checked advisory file back; the engine validates and imports it. The AI side holds no trading or broker credentials. | Owner decision Q6; no extra charge; keeps AI and order paths physically separate (document 11 §5). |

## Owner decisions (recorded 2026-10-23)

| ID | Topic | Decision |
|----|-------|----------|
| Q1 | Approve the design | **Approved** (owner instruction "続けてください", 2026-10-23). Implementation starts with TASK-001. **Until the owner says otherwise, Claude implements** instead of Codex; `AGENTS.md` and `.ai/CURRENT_TASK.md` are created only when the owner switches to Codex. |
| Q2 | Market data for BACKTEST and PAPER | Free Alpaca paper-only account, used **only** as a read-only data source. The owner signs up when Phase 2 starts. |
| Q3 | Where the system runs | Google Cloud for both PAPER and LIVE, in separate projects. The owner's always-on home PC is used for development and BACKTEST only. Projects are created when Phase 3 (PAPER) and Phase 7 (LIVE) start, each with a separate approval. |
| Q4 | Critical-alert channels | Email + ntfy (push). |
| Q5 | After the 5% monthly loss halt | Resume only after the owner re-enables manually. |
| Q6 | AI analysis | Runs as a **Claude Code scheduled task on the owner's subscription** (no extra charge), model **Claude Opus 5.5**. If it ever has to switch to the pay-as-you-go API, a hard cap of USD 10/month applies and the switch needs a new approval. |
| Q7 | Webull account and OpenAPI application | After PAPER has run stably (Phase 6). |
| Q8 | Webull 2FA for the API | Enabled for LIVE. |
| Q9 | Tax account for LIVE | Tokutei account with withholding (`SPECIFIC`). |
| Q10 | Webull sandbox contract tests | Allowed as soon as the design is approved. |
| Q11 | Daily loss limit | 1.5% of start-of-day equity; clears automatically at the next session. |
| Q12 | Drawdown halt | 10% from the equity peak; all new entries stop until the owner reviews. |
| Q13 | Merge rule | As proposed in document 06 §3: Claude merges when CI is green, acceptance criteria are met and no review findings are open; PRs touching risk limits, LIVE or new paid services need the owner's approval. |
| Q14 | Initial instruments | Liquid US ETFs only; single stocks are considered later. |

Approvals that remain separate and are asked for at the time: Alpaca sign-up (Phase 2), Google Cloud projects (Phase 3, Phase 7), creating the AI scheduled task and its data repository (Phase 4), Webull account and API keys (Phase 6), LIVE start (Phase 7), every later limit increase.

## Change log

| Version | Date | Change |
|---------|------|--------|
| 0.1 | 2026-10-23 | First draft for owner review. |
| 0.2 | 2026-10-23 | Webull Securities Japan as first-choice broker; research document 14; three-environment split (BACKTEST / PAPER virtual portfolio / LIVE disabled) with the Webull Sandbox for adapter tests only; new rules for authentication expiry, data delay and order errors; full cost recording; no production order authority for Claude/Codex. |
| 0.3 | 2026-10-23 | Owner decisions Q2–Q14 recorded; AI analysis moved to a Claude Code scheduled task on the subscription, exchanging data with the engine through a git data repository; Google Cloud for PAPER and LIVE; email + ntfy alerts; ETF-only universe; costs and checklists updated. |
