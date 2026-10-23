# 08. Cost Estimate (Initial and Monthly)

All figures are estimates. USD/JPY = 155 [A]. Prices checked 2026-10-23 unless marked. Costs that already exist outside this project (your Claude and Codex subscriptions, your PC) are **not** included.

## 1. Initial costs

| Item | Cost | Note |
|---|---|---|
| Design and development | ¥0 extra | Uses existing Claude/Codex subscriptions |
| Read-only data source (Alpaca paper-only account) | ¥0 | Email sign-up [V] (needs approval Q2) |
| Cloud project set-up | ¥0 | A billing account (card) is required even for the free tier [U for exact steps] (needs approval Q3) |
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
| Notifications | USD 0 (email, ntfy, Discord or Telegram) [U] | USD 0 | — |
| Separate LIVE project (Phase 7+) | + same again | + same again | LIVE runs on its own VM |

### 2.2 Market data and news

| Item | Cost | Note |
|---|---|---|
| Alpaca Basic data (IEX real-time, consolidated history > 15 min old) | USD 0 [V] | Enough for daily-bar strategies |
| Webull data in LIVE | USD 0: Nasdaq Basic via a (free) OpenAPI market data subscription [V] | TotalView and other advanced data are paid [U price] — not needed |
| Static IP for the Webull IP allow-list (LIVE VM) | a few USD/month [U] | Only for the LIVE project |
| News / filings / macro | USD 0 (SEC EDGAR, FRED; Alpaca news if free) [U] | Paid news is not justified at this capital |

### 2.3 AI (LLM) costs

Official Anthropic prices [V] (per million tokens, input / output): Claude Opus 5.5 USD 4 / 20; Claude Sonnet 5.5 USD 2 / 10; Claude Haiku 5.5 USD 0.10 / 0.50 (prompts up to 100k tokens). The Batch API halves both [V]. Cache hits cost 5% of the input price on Opus 5.5 and Sonnet 5.5 [V]. Reasoning (thinking) tokens are billed as output.

Workload assumption [A]: 1 pre-market analysis per trading day, ~21 trading days/month, about 60k input tokens (headlines, filings excerpts, price summary) and about 10k output tokens (including reasoning) per day, sent through the Batch API when timing allows.

| Model | Per day (standard) | Per month (standard) | Per month (batch) |
|---|---|---|---|
| `claude-opus-5-5` (design default) | 0.24 + 0.20 = USD 0.44 | ≈ USD 9.2 (≈ ¥1,430) | ≈ USD 4.6 (≈ ¥710) |
| `claude-sonnet-5-5` | USD 0.22 | ≈ USD 4.6 | ≈ USD 2.3 |
| `claude-haiku-5-5` | USD 0.011 | ≈ USD 0.23 | ≈ USD 0.12 |

During P4 (A/B test) the AI also runs the same analysis on shadow books, roughly doubling calls. A hard **monthly AI budget cap** (proposed USD 10, owner decision Q6) is enforced in code: when the cap is reached the AI overlay switches off and the rule-based baseline continues. The choice of a cheaper model is your decision; the design keeps the model configurable.

### 2.4 Total monthly cost scenarios

| Scenario | Infra | Data | AI | Total (USD) | Total (JPY) |
|---|---|---|---|---|---|
| A. Minimal (free-tier VM, rule-based only) | 0–5 | 0 | 0 | 0–5 | ¥0–780 |
| B. Minimal + AI (Opus 5.5, batch) | 0–5 | 0 | ≈ 5 | ≈ 5–10 | ≈ ¥780–1,550 |
| C. Standard (e2-small + AI Opus 5.5, standard calls) | ≈ 14–18 | 0 | ≈ 9 | ≈ 23–27 | ≈ ¥3,600–4,200 |
| D. Standard + LIVE project (Phase 7) | ≈ 28–36 | 0 | ≈ 9 | ≈ 37–45 | ≈ ¥5,700–7,000 |

## 3. What this means for a ¥50,000–100,000 account

| Capital | Monthly target 1–2% | Scenario A | Scenario B | Scenario C |
|---|---|---|---|---|
| ¥50,000 | ¥500–1,000 | 0–156% of target | 78–310% of target | 360–840% of target |
| ¥100,000 | ¥1,000–2,000 | 0–78% of target | 39–155% of target | 180–420% of target |

Break-even capital, where monthly costs equal a 1% monthly return:

| Scenario | Monthly cost | Break-even capital at 1%/month | At 2%/month |
|---|---|---|---|
| A (upper end) | ¥780 | ¥78,000 | ¥39,000 |
| B (upper end) | ¥1,550 | ¥155,000 | ¥78,000 |
| C | ¥3,900 | ¥390,000 | ¥195,000 |

Conclusions:

1. At ¥50,000–100,000 the **operating costs are of the same size as, or larger than, the profit target**. Net results after costs will likely be around zero or negative even if the strategy meets the 1–2% target, which itself is not assumed to be achievable.
2. The small live phase is therefore a **technical and execution-quality test**. Its success criteria are correct execution, clean reconciliation and realistic costs — not profit.
3. To keep costs low in LIVE_MICRO: free-tier VM, daily-bar cadence, batch AI calls, and the AI overlay only if P4 shows it is worth its cost.

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
  - AI: computed per call from the API's reported token usage and the price table in config; reconciled monthly with the provider invoice.
  - SERVER and DATA: monthly invoice amounts entered by the operator (or imported from billing exports), allocated per day.
- Reports show three lines separately: **trading P&L**, **operating costs**, **net**.
