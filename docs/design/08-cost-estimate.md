# 08. Cost Estimate (Initial and Monthly)

All figures are estimates. USD/JPY = 155 [A]. Prices checked 2026-10-23 unless marked. Costs that already exist outside this project (your Claude and Codex subscriptions, your PC) are **not** included.

## 1. Initial costs

| Item | Cost | Note |
|---|---|---|
| Design and development | ¥0 extra | Uses existing Claude/Codex subscriptions |
| Read-only data source (Alpaca paper-only account) | ¥0 | Email sign-up by the owner at Phase 2 [V] (decision Q2) |
| Cloud project set-up | ¥0 | A billing account (card) is required even for the free tier [U for exact steps] (Google Cloud decided, Q3; project creation approved at Phase 3 / Phase 7) |
| Webull account opening and OpenAPI application (Phase 6) | ¥0 | Account opening/maintenance and API use are free [V] |
| Initial JPY→USD conversion of ¥50,000 | ≈ ¥48 | JPY 0.15 per USD [V]; ≈ ¥96 for ¥100,000 |
| Initial live capital (Phase 7) | ¥50,000 (cap ¥100,000) | This is capital, not a cost |

## 2. Monthly running costs

### 2.1 Infrastructure

| Item | Low | Standard | Note |
|---|---|---|---|
| VM | USD 0 (e2-micro free tier: 1 instance/month in us-east1/us-central1/us-west1, 30 GB disk, 1 GB egress) [V] | ≈ USD 13 (e2-small, 2 GB RAM) [A] | e2-micro has 1 GB RAM: fine for the engine with REST brokers, too small for Java gateways (IB Gateway) |
| Public IPv4 address | USD 0–4 [U] | USD 0–4 [U] | Check whether the free tier covers it |
| Backups (object storage) | ≈ USD 0–1 [A] | ≈ USD 1 [A] | A few GB |
| Watchdog / heartbeat monitor | USD 0 (free tier of a SaaS or cloud function) [U] | USD 0–5 [U] | — |
| Notifications | USD 0 (email + ntfy, decision Q4) [U] | USD 0 | — |
| Separate LIVE project (Phase 7+) | ≈ USD 6–7 for an e2-micro outside the free tier [A] + static IP ≈ USD 3–4 [U] | ≈ USD 13 (e2-small) + static IP | The free tier covers only one e2-micro per billing account |

### 2.2 Market data and news

| Item | Cost | Note |
|---|---|---|
| Alpaca Basic data (IEX real-time, consolidated history > 15 min old) | USD 0 [V] | Enough for daily-bar strategies |
| Webull data in LIVE | USD 0: Nasdaq Basic via a (free) OpenAPI market data subscription [V] | TotalView and other advanced data are paid [U price] — not needed |
| Static IP for the Webull IP allow-list (LIVE VM) | a few USD/month [U] | Only for the LIVE project |
| News / filings / macro | USD 0 (SEC EDGAR, FRED; Alpaca news if free) [U] | Paid news is not justified at this capital |

### 2.3 AI costs

**Decision (Q6):** AI analysis runs as a Claude Code scheduled task on the owner's existing subscription with Claude Opus 5.5. Scheduled tasks draw from the subscription's usage limits and carry **no extra charge** [V: Claude Code docs, 2026-10-23]. Marginal AI cost is therefore **¥0**, recorded as `AI_SUBSCRIPTION = 0` in `operating_costs`; the subscription fee itself is an existing cost and is not allocated to this project. Two side effects are not money but matter:

- The runs use the same usage allowance as development work with Claude. A day on which the limit is reached simply has no advisory (baseline trading).
- The feature is a research preview; terms may change. A change that adds a charge triggers the fallback decision below.

Fallback (pay-as-you-go API, only after a new approval). Official prices [V] per million tokens (input / output): Claude Opus 5.5 USD 4 / 20; Claude Sonnet 5.5 USD 2 / 10; Claude Haiku 5.5 USD 0.10 / 0.50. The Batch API halves both. Workload assumption [A]: one analysis per trading day, ~21 days/month, ~60k input and ~10k output tokens (including reasoning).

| Model | Per month (standard) | Per month (batch) |
|---|---|---|
| `claude-opus-5-5` | ≈ USD 9.2 (≈ ¥1,430) | ≈ USD 4.6 (≈ ¥710) |
| `claude-sonnet-5-5` | ≈ USD 4.6 | ≈ USD 2.3 |
| `claude-haiku-5-5` | ≈ USD 0.23 | ≈ USD 0.12 |

In fallback mode a hard **USD 10/month cap** (decision Q6) is enforced in code: when it is reached, the AI overlay switches off and the rule-based baseline continues.

### 2.4 Total monthly cost scenarios (owner decisions applied)

| Phase | What runs | Infra (USD) | Data | AI | Total (USD) | Total (JPY) |
|---|---|---|---|---|---|---|
| P1–P2 (development, BACKTEST) | Home PC + CI | 0 | 0 | 0 | 0 | ¥0 |
| P3–P6 (PAPER) | GCP project `aitrader-paper`: e2-micro free tier, IPv4 0–4 [U], backups 0–1 | 0–5 | 0 | 0 (subscription) | 0–5 | ¥0–780 |
| P7+ (LIVE_MICRO + PAPER) | PAPER as above + `aitrader-live`: e2-micro outside the free tier ≈ 6–7 [A], static IP ≈ 3–4 [U], backups ≈ 1 [A] | ≈ 10–17 | 0 | 0 (subscription) | ≈ 10–17 | ≈ ¥1,550–2,640 |
| P7+ with the PAPER project stopped | LIVE only | ≈ 10–12 | 0 | 0 | ≈ 10–12 | ≈ ¥1,550–1,860 |
| Fallback: API instead of subscription | add | — | — | ≤ 10 (cap) | + up to 10 | + up to ¥1,550 |

The free tier covers one e2-micro instance per billing account per month [V], so the second (LIVE) VM is paid.

## 3. What this means for a ¥50,000–100,000 account

| Capital | Monthly target 1–2% | PAPER phase (¥0–780) | LIVE phase (≈ ¥1,550–2,640) |
|---|---|---|---|
| ¥50,000 | ¥500–1,000 | 0–156% of target | 155–528% of target |
| ¥100,000 | ¥1,000–2,000 | 0–78% of target | 78–264% of target |

Break-even capital, where monthly costs equal the monthly return:

| Monthly cost | At 1%/month | At 2%/month |
|---|---|---|
| ¥780 (PAPER phase, upper end) | ¥78,000 | ¥39,000 |
| ¥1,550 (LIVE only, lower end) | ¥155,000 | ¥78,000 |
| ¥2,640 (LIVE + PAPER, upper end) | ¥264,000 | ¥132,000 |

Conclusions:

1. At ¥50,000–100,000 in LIVE, **operating costs are of the same size as, or larger than, the profit target**. Net results after costs will likely be around zero or negative even if the strategy meets the 1–2% target, which itself is not assumed to be achievable.
2. The small live phase is therefore a **technical and execution-quality test**. Its success criteria are correct execution, clean reconciliation and realistic costs — not profit.
3. Running AI on the subscription removes the AI cost; the remaining LIVE cost is the second VM and its static IP. Stopping the PAPER project during LIVE saves up to ≈ ¥780/month but loses the parallel paper comparison — to be decided at Phase 7.

## 3a. Trading costs at Webull (recorded as trading P&L, not operating cost)

| Cost | Amount [V] | Per ¥50,000 account, typical month [A] |
|---|---|---|
| Commission | 0 | 0 |
| CAT fee | USD 0.000003 per share, buys and sells | ≈ 0 |
| FINRA fee | USD 0.000195 per share sold; min USD 0.01, max USD 9.79 per trade; whole and fractional parts charged separately | ≈ USD 0.02–0.03 per sale; ≈ USD 0.1–0.3/month at 4–10 sales |
| SEC fee | USD 0.0000206 per USD sold, rounded up to the cent | ≈ USD 0.01 per sale |
| FX spread | JPY 0.15 per USD per conversion | ≈ ¥48 per ¥50,000 converted (one-off when funding; again when withdrawing) |
| JPY withdrawal | ¥110 (MUFG) / ¥330 / ¥440 | only when money is taken out |

## 4. How costs are recorded

- Trading P&L (prices, commissions, regulatory fees, FX, dividends) is recorded in the ledger's trading accounts. Each fee is its own row in `trade_costs` (taken from Webull's per-order `commission` and `fees[]` breakdown when available, otherwise estimated by the `webull_jp` cost model and marked as estimated); each JPY⇄USD conversion is a row in `fx_conversions` with the applied rate, the reference rate and the spread cost.
- Operating costs go to a separate `operating_costs` table with categories **AI**, **SERVER**, **DATA**, **OTHER**:
  - AI: subscription mode records 0 per run (with the run reference); fallback API mode computes each call from the reported token usage and the price table in config and reconciles monthly with the invoice.
  - SERVER and DATA: monthly invoice amounts entered by the operator (or imported from billing exports), allocated per day.
- Reports show three lines separately: **trading P&L**, **operating costs**, **net**.
