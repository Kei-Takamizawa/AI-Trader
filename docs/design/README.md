# AI-Trader Design Package (v0.2, draft for owner review)

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
| D-08 | Cloud VM in a US region (Google Cloud Compute Engine `us-east1` proposed), managed by systemd. | Runs while the owner's Mac sleeps; close to US markets; free tier available for the smallest VM. |
| D-09 | SQLite (WAL mode) for state, JSON Lines structured logs, Parquet for market data. | Single-node, file-based, easy to back up and to inspect. |
| D-10 | Economic gate: if small-capital trading cannot pass the cost and risk ratios in document 05, the system stays in PAPER. | Requirement: do not force trades under the ¥100,000 cap. |
| D-11 | Live capital: start at ¥50,000, hard cap ¥100,000. Sizing uses min(actual equity, approved capital cap), so extra deposits never increase risk. | v0.2 requirement. |
| D-12 | Cash account, long only, US stocks/ETFs, regular hours, fractional orders by amount where the instrument is `fractionable`. `SHORT`, margin, options and extended hours are blocked by hard caps. | v0.2 requirement; document 14. |
| D-13 | Every cost is recorded: commission, CAT/FINRA/SEC fees (from Webull's order fee breakdown), FX spread on each JPY⇄USD conversion, ADR and withdrawal fees — separately from operating costs. | v0.2 requirement. |
| D-14 | New entries stop automatically on API authentication problems (token not `NORMAL`, key expiry near/unknown, 401/403, event-stream auth errors), on data delay, and on order errors. | v0.2 requirement; document 05 sections 4, 8 and 16. |
| D-15 | Claude and Codex never receive production order authority: no production Webull keys in any development environment; Webull MCP/CLI/Agent Skills are never connected to a production account; development uses fixtures and the public sandbox only. Only the owner can generate production keys (SMS + trading password). | v0.2 requirement; document 06. |
| D-16 | The Webull SDK is imported only inside `aitrader.broker.webull`. Everything else talks to the broker through the `BrokerPort` interface, so another broker can replace Webull later. | v0.2 requirement; document 03. |

## Decisions the owner needs to make

| ID | Question | Recommendation |
|----|----------|----------------|
| Q1 | Approve this design (or list changes). | — |
| Q2 | Market data source for BACKTEST and PAPER. Webull's own data would put a trading-capable production key on the paper host (document 14 §7). | Free Alpaca paper-only account used **only** as a read-only data source (email sign-up, approval needed when Phase 2 starts). Phase 1 needs no external account. |
| Q3 | Approve creating a Google Cloud project (needs a billing account even for the free tier) in Phase 3. | Approve when Phase 3 starts. |
| Q4 | Notification channels for critical alerts (at least two independent channels). | Email plus one push channel (ntfy, Discord or Telegram). |
| Q5 | After the 5% monthly loss halt: resume automatically next month, or only after manual re-enable? | Manual re-enable. |
| Q6 | AI model and monthly AI budget cap. | Default model `claude-opus-5-5`; hard budget cap USD 10/month during PAPER. Cheaper models are listed in document 08 for your decision. |
| Q7 | When to open the Webull account and apply for OpenAPI (Phase 6). | Only after PAPER has run stably; Webull sandbox tests need no account. |
| Q8 | Enable Webull 2FA for the API (adds a phone confirmation when a token is created; token lapses after 15 days without calls). | Enable for LIVE. |
| Q9 | Tax account for LIVE orders: tokutei (`SPECIFIC`) or general (`GENERAL`). | Tokutei account; withholding choice is yours. |
| Q10 | Allow the operator to run the Webull sandbox contract tests (public shared test accounts, no personal data) after design approval. | Approve together with the design. |

## Change log

| Version | Date | Change |
|---------|------|--------|
| 0.1 | 2026-10-23 | First draft for owner review. |
| 0.2 | 2026-10-23 | Webull Securities Japan as first-choice broker; research document 14; three-environment split (BACKTEST / PAPER virtual portfolio / LIVE disabled) with the Webull Sandbox for adapter tests only; new rules for authentication expiry, data delay and order errors; full cost recording; no production order authority for Claude/Codex. |
