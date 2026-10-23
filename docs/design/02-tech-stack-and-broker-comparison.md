# 02. Tech Stack and Broker / Data API Comparison

Research date: **2026-10-23**. Tags: **[V]** verified on an official page today, **[S]** secondary source only, **[U]** unverified or not found, **[A]** planning assumption. Sources are listed at the end.

> **v0.2 update:** the owner chose **Webull Securities Japan** as the first-choice broker. A detailed review of the Webull OpenAPI is in [document 14](14-webull-openapi-research.md); it supersedes the Webull entries in the tables below where they differ (notably: fractional orders by amount **are** supported by the API, tokutei accounts **are** supported, FX is JPY 0.15 per USD on the official pricing page, and the FSA registration number is No. 48).

## 1. What the broker must satisfy

Hard requirements (from the brief):

1. Legally usable by a Japan resident (a broker registered with the Japanese FSA is strongly preferred: investor protection fund, Japanese tax documents, Japanese support).
2. Python-accessible API that can trade US stocks/ETFs **without per-trade human confirmation**.
3. Can run unattended on a cloud server.
4. Economically workable with **¥50,000 (first choice) to ¥100,000 (cap)**: cash account, no leverage, low frequency, fees included in a 0.5% per-trade risk budget.

## 2. Candidates

| Candidate | Why considered | Short verdict |
|---|---|---|
| Interactive Brokers Securities Japan (IBSJ, "IB証券") | Most mature API in the industry; Japanese entity. | Technically strongest, but **minimum commissions make ¥50k–100k uneconomic**; weekly manual 2FA re-login. |
| moomoo Securities Japan ("moomoo証券") | Japanese entity with an OpenAPI and a paper environment; percentage-based fees. | Promising, **but official pages conflict on whether the API trades US stocks or only US options** for Japanese accounts. |
| Webull Securities Japan ("ウィブル証券") | Japanese entity with a documented OpenAPI for US equities; US stock commissions waived from 2026-07-27. | **Best economics on paper**; fractional orders via API not documented. |
| Saxo Bank Securities Japan ("サクソバンク証券") | Japanese entity with OpenAPI. | API offered without support; US stock API scope not confirmed. Reserve candidate. |
| Alpaca | Best developer experience; free global paper accounts. | **Paper and data only.** No official confirmation that Japan-resident individuals can open live accounts; Alpaca's Japan licence is used for business partners (e.g. SBI Securities). |
| Large Japanese online brokers (SBI, Rakuten, Monex, etc.) | Popular in Japan. | No public trading API for US stocks found in this research **[U]**. Not usable. |

## 3. Detailed comparison

### 3.1 Japan eligibility, API and unattended operation

| Item | IBSJ | moomoo JP | Webull JP | Saxo JP | Alpaca |
|---|---|---|---|---|---|
| Japan-registered broker | Yes, FIBO No. 187 [V: FSA registry] | Yes, Kanto Local Finance Bureau (FIBO) No. 3335 [V] | Yes, FIBO No. 48 [V: FSA registry] | Yes [S] | Japan licence for B2B infrastructure [V]; direct retail for Japan residents [U] |
| API for US stocks | TWS API (Python), Web API (REST + WebSocket), FIX [V] | OpenAPI via local gateway "OpenD" or cloud REST [V]. **Conflict:** one official page says Japanese customers can trade US stocks and US stock options via API, another says "currently only US stock options" [V, conflicting] | Trading API supports US equities and JP equities [V] | OpenAPI for "foreign stocks etc." [V]; US stocks not named | Excellent REST + WebSocket [V] |
| Order types documented | Full IBKR set [U for IBSJ specifics] | Market, limit, stop, stop-limit, trailing and touch types [V] | Market, limit, stop, stop-limit; DAY / GTC [V] | [U] | Full set [V] |
| Idempotency key | `orderRef` / perm IDs [U] | [U] | `client_order_id` required, unique per account [V] | [U] | `client_order_id` [V] |
| API rate limits | [U] | Place order: 15 requests / 30 s per account, ≥0.02 s apart [V] | Place / replace / cancel: 600 / 60 s; order queries 2 / 2 s [V] | [U] | 200 data calls/min on free plan [V] |
| Paper / test environment | Free trial demo account, **market data delayed ≥10 min** [V]; paper account linked to a funded live account [U for IBSJ] | Demo (paper) trading through the same API, no trade unlock needed; US paper has no extended hours [V] | Test environment with shared test accounts; no approval needed for test [V] | [U] | Paper-only account for anyone worldwide with an email [V] |
| API access approval | Account required [V] | Agree to API terms; integrated account required for trading [V] | Application, reviewed in 1–2 business days [V] | "Contact us"; free for private use; **no support** [V] | Sign-up [V] |
| Unattended operation | TWS/IB Gateway must restart daily and **re-login manually once a week** (2FA) [V, IBKR docs] | OpenD runs on Linux/Windows/macOS [V]; live trading needs a trade-unlock password [V] | Token becomes invalid after 15 days without API calls; optional 2FA via the mobile app [V] | [U] | Fully unattended [V] |
| Per-trade human confirmation | Not required [U for IBSJ] | Not stated for OpenAPI [U]. (moomoo's separate "API Skill" product requires manual confirmation for live trades [V]) | Not stated [U] | [U] | Not required [V] |

### 3.2 Costs for a small account

| Item | IBSJ | moomoo JP | Webull JP | Saxo JP | Alpaca |
|---|---|---|---|---|---|
| US stock commission | Fixed: USD 0.005/share, **min USD 1.00/order**, max 1% of trade value. Tiered: USD 0.0035/share (≤300k shares/month), **min USD 0.35**, plus exchange/clearing fees. Includes 10% Japanese consumption tax. Tiered not available for direct-routed API orders [V] | Basic course: **0.132% of trade value** (tax incl.), cap USD 22; amounts under USD 0.01 round up to 0.01 [V] | **USD 0 from 2026-07-27** (announced as permanent; date marked "planned") [V]; third-party fees (SEC, FINRA, CAT) passed through [V] | 0.088% min USD 1.10 [S] | USD 0 (not usable for live from Japan) |
| Fractional shares | Available for US stocks/ETFs after enabling [V]; via API for IBSJ [U]; "non-recurring fractional trades: minimum commission USD 0.01" stated, meaning unclear [V text, U meaning] | "micro US stocks" with same 0.132% fee [V]; API order quantity accepts 3 decimals (global docs) [V]; for Japanese accounts via API [U] | **Via API by amount (`entrust_type: AMOUNT`) or decimal quantity**; buy ≥ USD 1, step 0.00001; regular hours; market or marketable limit only [V, document 14] | [U] | Yes [V] |
| FX (JPY ⇄ USD) | Automatic conversion: 0.03% added to the rate, no separate fee; manual conversion to JPY: min USD 2.00 equivalent [V] | Real-time FX "free", but the rate **includes a spread** of unstated size; may become paid [V] | **JPY 0.15 per USD** per conversion [V] (≈0.1% at ¥155) | [U] | n/a |
| Minimum deposit / balance | No account maintenance fee or minimum balance requirement [V] | Not stated [U] | Account opening and maintenance free [V]; minimum deposit not stated [U] | Individuals: none (API page) [V] | n/a |
| Real-time US data | Free real-time streaming from Cboe One and IEX (not consolidated) [V]; consolidated NBBO bundle USD 10/month (waived at USD 30 monthly commissions) + USD 4.50 add-on [V] | API data rights separate from the app; some data paid [V]; US LV3 free "during promotion" (global docs) [V], applicability to Japan [U] | Nasdaq Basic free for OpenAPI (separate subscription from the app; one device at a time) [V] | [U] | IEX real-time free; full SIP USD 99/month [V] |
| Tax account type | **General account only; no tokutei (特定) account** [V] → self-reporting every year | [U] | **Tokutei and general accounts** [V]; API orders state `account_tax_type` [V] | [U] | n/a |

### 3.3 Other notes

- **US pattern-day-trader rule:** the SEC approved FINRA's change to Rule 4210 on 2026-04-14, effective 2026-06-04, with brokers allowed to phase it in until 2027-10-20 [S]. It does not matter for this design (cash account, low frequency), but broker rules for **unsettled cash** (T+1 settlement in the US) still apply and are modelled in the ledger.
- **Japanese tax:** gains on US stocks are taxed in Japan; a general account means the owner files a tax return. US dividends have US withholding. This design records every trade and dividend so a tax report can be produced; it is not tax advice.

## 4. Economic feasibility at ¥50,000 and ¥100,000

Assumptions: USD/JPY = **155** [A] (observed around 156 in mid-September 2026 [S]); stop distance 4% of entry (about 3–4 daily ATRs of a broad-market ETF) [A]; spread + slippage 5 bp per side for liquid ETFs [A]; USD held in the account after the first conversion (no FX cost per trade) [A].

Per-trade risk budget (0.5% of equity, fees included):

| Capital | In USD | 0.5% risk budget | Monthly 5% loss limit | Monthly target 1–2% |
|---|---|---|---|---|
| ¥50,000 | ≈ USD 323 | ≈ ¥250 = USD 1.61 | ¥2,500 | ¥500–1,000 |
| ¥100,000 | ≈ USD 645 | ≈ ¥500 = USD 3.23 | ¥5,000 | ¥1,000–2,000 |

Largest position that fits the budget, and how much of the budget fees consume (¥50,000 account):

| Broker (plan) | Round-trip cost model | Max position | Round-trip cost | Cost share of risk budget |
|---|---|---|---|---|
| Webull JP | ~USD 0.02 regulatory + 0.10% slippage | ≈ USD 39 | ≈ USD 0.06 | **≈ 4%** |
| moomoo JP (Basic) | 0.264% commission + 0.10% slippage | ≈ USD 37 | ≈ USD 0.13 | **≈ 8%** |
| IBSJ (Fixed) | min(USD 1.00, 1% of value) per side + slippage | ≈ USD 26 | ≈ USD 0.55 | **≈ 34%** |
| IBSJ (Tiered) | min(USD 0.35, cap) per side + fees + slippage | ≈ USD 26 | ≈ USD 0.55 | **≈ 34%** |

The proposed rule (document 05, R-ECON-1) is that expected round-trip cost must not exceed **20%** of the risk budget. Under these assumptions:

- **Webull JP and moomoo JP pass** at ¥50,000 and ¥100,000 — *if* the API can trade fractional shares or the universe is restricted to ETFs priced below the position size (≈ USD 37 at ¥50,000, ≈ USD 75 at ¥100,000). Whole-share-only trading at ¥50,000 leaves very few eligible instruments, so **fractional orders through the API are decisive**. For Webull this is now verified in the API reference (document 14); the universe is restricted to instruments flagged `fractionable`.
- **IBSJ fails** at both sizes, because the minimum commission is about 1% of a sub-USD 100 order per side. By the same model (position ≈ 10% of equity, costs ≤ 20% of the budget) IBSJ becomes workable from roughly ¥130,000 (Tiered) or ¥350,000 (Fixed) [A].

Operating costs are a separate and larger problem at this size (see document 08): even the cheapest setup costs about the same as the monthly profit target of a ¥50,000 account. **The small live phase is a technical and execution-quality test, not an income source.**

## 5. Recommendation

| Purpose | Choice (v0.2) | Status |
|---|---|---|
| BACKTEST data | Alpaca historical data (free plan, consolidated history older than 15 minutes), used read-only, with a second source for cross-checks | Needs Alpaca sign-up (owner approval Q2) |
| PAPER | Our own virtual portfolio (`SimBroker` + ledger) with live data from the same read-only source and the `webull_jp` cost/fractional profile | No broker account needed |
| Broker adapter tests | Webull Sandbox (shared public test accounts) | After design approval (Q10) |
| LIVE | **Webull Securities Japan** via the official Python SDK; disabled until approval | Owner decision; open points in document 14 §8 |
| Fallbacks | moomoo JP (percentage fees) and IBSJ (needs more capital) — adapters only if Webull fails verification | Not planned |

PAPER results do **not** predict live results: real fills, liquidity and Webull's order handling can differ from our fill model. The LIVE_MICRO stage exists for that reason.

## 6. Questions to confirm with the broker (Phase 6)

For Webull the concrete list is in document 14, section 8 (sandbox checks SB-1 to SB-6, broker questions WB-1 to WB-8). The generic list below applies to any broker that replaces Webull later. Written answers (support ticket or official documentation) to:

1. Can a Japan-resident individual trade **US stocks/ETFs** (not only options) through the API, fully automatically, without per-order confirmation? (moomoo: resolve the conflicting pages.)
2. Are **fractional / amount-based** orders available through the API? Minimum order value? Which order types and time-in-force values work with fractional orders?
3. Are **stop orders** accepted for fractional positions? Are they held at the broker overnight (GTC)?
4. Exact FX cost (spread or fee) and whether USD can be held.
5. Tax account type (tokutei account with withholding?) and annual tax documents.
6. API terms: permitted from a cloud server? IP restrictions? Session/token expiry and re-authentication needs?
7. Paper/test environment: real-time or delayed data? Same order rules as live?
8. Market data rights through the API and any monthly fees.
9. Settlement rules for reusing sale proceeds in a cash account.
10. Behaviour on API outage: can orders still be cancelled by phone or app? Support hours in JST.

## 7. Market data, news and macro sources

| Source | Use | Cost | Status |
|---|---|---|---|
| Alpaca Market Data, Basic plan | Daily/minute bars, IEX real-time quotes | Free: IEX only for real-time, 200 calls/min, 30 WebSocket symbols, consolidated history older than 15 min [V] | Primary for paper |
| Alpaca Algo Trader Plus | Full consolidated real-time feed | USD 99/month [V] | Not justified at this capital |
| Massive (formerly Polygon.io) | Second source, fundamentals | Plans quoted at USD 0 / 29 / 79 / 199 per month [S] | Optional later |
| Broker market data | Live trading quotes | See table 3.2 | Use the live broker's own data in LIVE |
| Alpaca News API (Benzinga) | Headlines and news bodies | Free availability and limits [U] | Candidate |
| SEC EDGAR | Filings (8-K, 10-Q, 10-K) | Free; fair-access limits and User-Agent header required [U] | Candidate |
| FRED (St. Louis Fed) | Macro series | Free with API key [U] | Candidate |
| Earnings calendar | Event-risk flags | Provider not chosen [U] | To research in Phase 4 |

## 8. Technology stack

| Area | Choice | Reason | Alternatives considered |
|---|---|---|---|
| Language | Python 3.14 preferred (3.13 fallback), managed with `uv`; final choice in TASK-001 after a dependency check | The Webull SDK supports 3.8–3.14 and allows current `cryptography`/`grpcio` versions only on 3.14 (document 14, U12) | 3.12 |
| Broker SDK | `webull-openapi-python-sdk`, exact version pinned; upgrades only after sandbox contract tests | Official SDK handles signing, token and gRPC/MQTT | Raw HTTP (more code, more risk) |
| Data frames / storage | pandas 2 + pyarrow, Parquet files | Mature, simple | polars (faster, not needed at this scale) |
| Config and schemas | pydantic v2, pydantic-settings, YAML files | Validation of every config value and every AI output | dataclasses + manual checks |
| State database | SQLite (WAL) via SQLAlchemy Core, Alembic migrations | Single node, zero cost, easy backup | PostgreSQL (managed DB adds about USD 10+/month) |
| Exchange calendar | `exchange_calendars` (XNYS) | Holidays, half-days, DST | `pandas_market_calendars` |
| Scheduling | Own scheduler on top of the calendar + systemd service | Explicit, testable with a simulated clock | APScheduler |
| HTTP | httpx with explicit timeouts; tenacity for retries | Predictable failure behaviour | requests |
| Logging | structlog, JSON Lines | Machine-readable, Muse-ready | stdlib logging |
| CLI | typer | Simple commands: backtest, paper, status, kill, reconcile | click |
| Read-only status API (Phase 5) | FastAPI on a private interface | Small, typed | static files only |
| Tests | pytest, hypothesis (property tests for risk), time-machine, respx (HTTP mocks), pytest-cov | Fault and edge cases can be simulated | — |
| Static checks | ruff (lint + format), mypy (strict for core packages), import-linter (dependency rules) | Enforces "risk does not import AI" mechanically | — |
| CI | GitHub Actions | Runs on every pull request | — |
| LLM | Anthropic Python SDK, structured outputs; Message Batches for non-urgent analysis | Schema-checked outputs; batch is 50% cheaper [V] | — |
| Data SDK | `alpaca-py` market data client only (no trading calls), if Q2 is approved | Read-only data for BACKTEST/PAPER | Another data vendor |
| Notifications | Email (SMTP) + one push channel (ntfy / Discord / Telegram webhook) | Two independent channels | LINE Notify (service ended in 2025) |
| Watchdog | External heartbeat monitor (SaaS free tier or a small cloud function) | Different failure domain from the VM | — |
| Hosting | Google Cloud Compute Engine, `us-east1`; free tier e2-micro (1 instance/month in us-west1, us-central1 or us-east1) [V] | Low cost, US region | AWS Lightsail, Hetzner |

## 9. Sources (accessed 2026-10-23)

Interactive Brokers Securities Japan (official):
- Commissions overview: https://www.interactivebrokers.co.jp/jp/pricing/commissions-home.php
- US stock commissions and third-party fees: https://www.interactivebrokers.co.jp/jp/pricing/commissions-stocks.php?region=americas
- Currency conversion: https://www.interactivebrokers.co.jp/jp/pricing/commissions-spot-currencies.php
- Market data pricing: https://www.interactivebrokers.co.jp/jp/pricing/market-data-pricing.php
- Individual accounts (general account only, no tokutei): https://www.interactivebrokers.co.jp/jp/accounts/individual.php
- API overview: https://www.interactivebrokers.co.jp/jp/trading/ib-api.php
- Fractional shares: https://www.interactivebrokers.co.jp/jp/trading/fractional-trading.php
- Free trial (demo data delayed ≥10 min): https://www.interactivebrokers.co.jp/jp/trading/free-trial.php
- Daily and weekly re-authentication (IBKR docs): https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/daily-weekly-reauthentication

moomoo Securities Japan (official):
- Pricing: https://www.moomoo.com/jp/pricing
- US stock fees: https://www.moomoo.com/jp/support/topic7_184
- OpenAPI guide for US stocks: https://www.moomoo.com/jp/learn/python-openapi-guide-for-us-stocks
- OpenAPI help (states "US stock options only"): https://www.moomoo.com/jp/support/topic7_474
- OpenAPI press page: https://www.moomoo.com/jp/newsroom/moomoo-openapi
- Moomoo API Skill release (live trades need manual confirmation): https://prtimes.jp/main/html/rd/p/000000155.000110773.html
- OpenAPI place-order reference: https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html
- OpenAPI authorities and limits: https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html

Webull Securities Japan:
- Commission-free US stocks from 2026-07-27 (official release): https://prtimes.jp/main/html/rd/p/000000073.000131836.html
- Trading API overview: https://developer.webull.co.jp/apis/docs/trade-api/overview
- Stock orders: https://developer.webull.co.jp/apis/docs/trade-api/stock
- Trading API FAQ: https://developer.webull.co.jp/apis/docs/trade-api/faq
- Token and 2FA: https://developer.webull.co.jp/apis/docs/authentication/token
- Market data API: https://developer.webull.co.jp/apis/docs/market-data-api/overview
- FX fee JPY 0.15/USD (secondary): https://kabukiso.com/column/news/2026/webll_commission_free.html

Saxo Bank Securities Japan:
- OpenAPI page: https://www.home.saxo/ja-jp/platforms/api

Alpaca (official):
- Paper trading: https://docs.alpaca.markets/us/docs/paper-trading
- Market data plans: https://docs.alpaca.markets/docs/about-market-data-api
- Non-US live accounts: https://alpaca.markets/learn/live-trading-account-non-us
- SBI Securities execution service: https://alpaca.markets/blog/alpaca-launches-us-stock-execution-service-for-sbi-securities-japans-largest-online-brokerage/
- SBI Holdings partnership and Japan licence: https://alpaca.markets/blog/alpaca-and-japans-sbi-holdings-announce-partnership-and-usd15-million-strategic-investment-to-accelerate-alpacas-asian-business/

Other:
- FSA registry of financial instruments business operators (IBSJ No. 187, Webull No. 48): https://www.fsa.go.jp/menkyo/menkyoj/kinyushohin.xlsx
- Full Webull source list: document 14, section 9
- Pattern-day-trader rule change (secondary): https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum
- Google Cloud free tier: https://docs.cloud.google.com/free/docs/free-cloud-features
- Anthropic API pricing: https://platform.claude.com/docs/en/about-claude/pricing
- USD/JPY level in September 2026 (secondary): https://longforecast.com/usd-jpy-forecast-2017-2018-2019-2020-2021-dollar-yen
