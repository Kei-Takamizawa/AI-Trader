# TASK-005: Backtest loop, exchange calendar, fills from bars, metrics, run manifest, CLI

Implementer: Claude (owner instruction 2026-10-23). Design sources: documents 02 (tech stack:
`exchange_calendars`), 03 (packages and dependency rules), 11 (§3 S2, §4 execution rules),
12 (§1 engine, §2 biases, §4 cost and fill models, §6 metrics, §7 run manifest).

Status: **done** (2026-10-24). 820 tests, 100% line and branch coverage; ruff, mypy (strict),
lint-imports (9 contracts) green; the CLI backtest is byte-identical across runs.

## Scope

1. **Calendar** (`aitrader.clock.TradingCalendar`): NYSE sessions, half days and DST from
   `exchange_calendars`, built for fixed bounds (2005–2030) so results never depend on the day
   a program runs; T+1 settlement dates. Only `aitrader.clock` may import the calendar library
   and pandas (new import contract).
2. **Data** (`aitrader.data`): daily bars from CSV files, refused when malformed; a data
   snapshot hash; the look-ahead guard (a bar is visible only after its session closed, a view
   raises `LookAheadError` for anything later); a quality gate with DQ-2 (completeness against
   the calendar) and the daily-move part of DQ-3.
3. **Indicators** (`aitrader.indicators`, new, domain-only): SMA, ATR, Wilder RSI, volatility,
   returns, in exact decimals; strategies may import them (dependency rule updated in doc 03).
4. **Strategy port and S2** (`aitrader.strategy`): `Strategy`, `StrategyContext`, `Holding`,
   parameter hash; **S2 pullback in an uptrend** (document 11 §3).
5. **Trading cycle** (`aitrader.ops.TradingCycle`), shared with PAPER: marketable-limit pricing
   (mid + 5 bp, capped at 1% over the previous close; exits mid − 5 bp; stop exits at market),
   risk engine, order gate and order manager, exits first, protective stops per position
   (tightened only), cancel of unfilled orders at the end of the execution window.
6. **Backtest loop** (`aitrader.backtest.run_backtest`): per session — gap stops at the open,
   execution window at 09:35, window end at 10:30, intraday stops, close; quotes built from the
   day's bar (half spread 1 bp) go through the **same SimBroker quote path as PAPER**; at most 1%
   of the bar's volume per order.
7. **Metrics** (`aitrader.metrics`): returns (total, CAGR, monthly, yearly, share of months
   ≥ 1% and ≥ 2%), drawdown and its duration, Sharpe and Sortino (0% risk-free rate for now),
   trade statistics, exposure, turnover, costs by type plus spread and slippage, trading P&L in
   USD and JPY, benchmarks B0 (buy and hold SPY with the same costs) and B1 (cash).
8. **Run manifest and CLI**: `aitrader backtest` writes `manifest.yaml`, `metrics.json` and
   `equity.csv`; the run ID is a hash of the manifest. CI runs it twice and compares the files.

## Out of scope

Real market data and the Parquet store (TASK-006, needs the owner's Alpaca sign-up), S1 and
the walk-forward runner (TASK-007), dividends and corporate actions in backtests, DQ-4 and DQ-5,
halt transitions from loss limits (the risk engine already blocks entries at those limits).

## Implementation notes

- **Fills from bars** use synthetic quotes rather than a separate bar fill model, so BACKTEST
  and PAPER share one execution path. Entry limits are marketable, so "fill at the limit"
  (document 12 §4) and "fill at the quote plus slippage, capped at the limit" give the same
  price in practice. Gaps through a stop fill at the open minus spread and slippage; a stop
  inside the day's range fills at the stop minus spread and slippage.
- **S2 was implemented here** (instead of TASK-007) so the loop and the CLI run a real
  strategy. TASK-007 keeps S1, the walk-forward runner and the research report.
- The fixture bars in `tests/fixtures/bars/` are **synthetic**; no result from them says
  anything about a strategy.
