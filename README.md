# AI-Trader

A personal, fully automated trading system for US stocks and ETFs, designed to run unattended in the cloud while its owner sleeps in Japan.

> **Current status: design stage.** No trading code exists yet. The system has never traded, and it will trade only simulated (virtual) money until the owner explicitly approves real-money trading.

## What it will do

- **Test strategies on historical data** (BACKTEST mode) with realistic fees, spreads and slippage.
- **Trade with virtual money on live market data** (PAPER mode), fully automatically during US market hours.
- **Later, trade a small amount of real money** (LIVE mode), only after a long checklist is complete and the owner approves. LIVE is switched off by default.
- **Use AI to read news, filings and market conditions.** The AI can only make the system more careful (skip or shrink a trade). It can never place or enlarge an order by itself.
- **Protect capital first.** A separate, rule-based risk program limits the loss per trade, per day and per month, forbids leverage and short selling, and stops new trading automatically when something looks wrong.
- **Report everything.** Every decision, order, fee and operating cost (AI, server, data) is recorded, and daily and monthly reports are produced automatically.

## What it will not do

- Promise profits. The 1–2% monthly return goal is a research target, not an expectation. Results on paper may not repeat with real money.
- Use leverage, margin, short selling or options.
- Change its own rules while running. Improvements are tested separately and released only after review.

## How to read the design

Start with the [design package overview](docs/design/README.md). It lists the documents, the key decisions and the questions the owner still needs to answer.

## Who works on it

- **Owner:** approves every important step (design, sign-ups, spending, real money).
- **Claude:** architecture, strategy research, task specifications, reviews.
- **Codex:** implementation and automated tests.
- **Muse (Meta AI):** planned later as a read-only operations assistant, if its availability and terms fit.
