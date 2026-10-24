# 12. Backtest and Evaluation Method

## 1. Engine design

- **Event-driven, our own small engine** in `aitrader.backtest`, driving the *same* strategy, risk engine, order manager and ledger as PAPER and LIVE through `SimClock` and `SimBroker`.
- Why not an existing framework (backtrader, zipline-reloaded, vectorbt)? Their strategy APIs would force a second implementation of the strategy and risk code, which defeats the "same code in every mode" rule. A vectorised library may be used **only** as an independent sanity check of results.

Daily cycle in the backtest mirrors production: end-of-day signal → next-session execution window → fills from the fill model → protective stops → end-of-day ledger and metrics.

Implementation (TASK-005): the fill model is applied by building quotes from each day's bar (the open for the execution window; the open or the stop level for stops) and feeding them to the `SimBroker` through the same quote path PAPER uses, so both modes share one execution code path.

## 2. Preventing look-ahead and other biases

| Bias | Control |
|---|---|
| Look-ahead | The data adapter never returns data stamped after `clock.now()`; decisions at close of day *t* execute in session *t+1*; unit test injects a future bar and expects an error |
| Adjusted-price leakage | Signals use split- and dividend-adjusted series computed only from information available at the time; fills use the unadjusted price of that day; dividends are booked as cash |
| Survivorship | ETF universe fixed in advance; delisted/merged ETFs kept in the data if they existed; single stocks only later with point-in-time membership |
| Data snooping / overfitting | Small pre-declared parameter grid; walk-forward; number of tried variants recorded; deflated Sharpe ratio reported |
| Hold-out contamination | The most recent 24 months are locked as a hold-out and evaluated **once** per strategy version, after the design is frozen |
| LLM knowledge leakage | AI overlay not backtested on periods before the model's training cutoff (document 11, section 7) |
| Calendar errors | Exchange calendar for holidays, half-days and DST |

Implemented in TASK-007a: point-in-time adjusted signals, raw fills and estimated dividends as cash.

## 3. Walk-forward protocol

Implemented in TASK-007b.

```
|---- train 36 months ----|-- test 6 months --|
          |---- train 36 months ----|-- test 6 months --|
                    |---- train 36 months ----|-- test 6 months --|
...                                                     | LOCKED HOLD-OUT (last 24 months) |
```

- Parameter choice uses training windows only; results are reported on the concatenated test windows.
- Stability check: results for neighbouring parameter values must not collapse (no "knife-edge" optimum).
- Final hold-out run once; the report states the number of variants tried before it.

Window lengths are [A] and can be adjusted in research, but must be declared before running.

## 4. Cost and fill models

| Model | Default | Notes |
|---|---|---|
| Commission | Default profile `webull_jp`: 0. Alternative profiles for comparison: moomoo JP 0.132% (cap USD 22); IBSJ Fixed / Tiered with minimums and 1% cap | Always applied; the report shows `webull_jp` first and the others for robustness |
| Regulatory fees | `webull_jp`: CAT USD 0.000003/share both sides; FINRA USD 0.000195/share sold (min 0.01, max 9.79, whole and fractional parts separately); SEC USD 0.0000206 per USD sold, rounded up to the cent | Small but modelled |
| FX | `webull_jp`: JPY 0.15 per USD per conversion; zero per trade when USD is held, charged on deposits/withdrawals | — |
| Fractional rules | `webull_jp`: regular hours only; buy ≥ USD 1; quantity step 0.00001; marketable limits only; no broker-side stops (system-side stop model) | — |
| Spread | Half-spread per side: 1–2 bp for the most liquid ETFs, larger for others (estimated from quote data where available) | Configurable per symbol |
| Slippage | 5 bp per side by default plus a volatility term (k × ATR fraction) | Calibrated later against LIVE_MICRO fills |
| Limit orders | Fill only if the day's range crosses the limit; fill price = limit (conservative: no price improvement) | — |
| Stops and gaps | If the open gaps through the stop, fill at the open (minus slippage), not at the stop | Models the main limitation of stops |
| Partial fills | Capped participation (≤ 1% of bar volume); remainder cancelled at window end | Rarely binding at this size |
| Minimum order / increments | Broker profile (fractional step or 1 share; minimum value) | Orders that round to zero are rejected, as in production |
| Settlement | T+1 for US stocks; unsettled proceeds not reused in cash-account mode | — |

## 5. Data quality for backtests

- Two sources for daily bars when possible (Alpaca historical as primary; a second provider for cross-checks).
- The same quality gate as production (document 05, section 4) runs over the whole history; quarantined bars and their effect are listed in the report.
- Each run records a **data snapshot hash** (hash of the exact Parquet files used).

## 6. Metrics (minimum set)

| Metric | Definition |
|---|---|
| Monthly and yearly returns | From daily equity, in USD and JPY (JPY includes the FX effect separately) |
| Maximum drawdown | Largest peak-to-trough decline of equity; plus duration and recovery time |
| Sharpe ratio | Annualised mean excess daily return / std (risk-free from short-term T-bill yield series) |
| Sortino ratio | As Sharpe, with downside deviation |
| Profit factor | Gross profit / gross loss of closed trades |
| Win rate | Winning trades / all closed trades |
| Payoff ratio | Average win / average loss |
| Exposure and turnover | Average invested fraction; traded value / average equity |
| Cost attribution | Commissions, spread/slippage, FX, regulatory fees — each in currency and as % of gross P&L |
| Benchmark comparison | Same metrics for B0 (buy-and-hold) and B1 (cash); excess return; correlation; beta |
| Monthly 1–2% target | Share of months ≥ 1% and ≥ 2%; distribution of monthly returns (the target is a reference, not an assumption) |
| AI on vs. off | Document 11, section 7 |
| Operating costs | AI, server and data costs per month, shown next to trading P&L; net result |
| Robustness | Deflated Sharpe ratio; results under each broker cost profile; results with doubled slippage |

## 7. Run manifest (reproducibility)

Every backtest writes a manifest next to its results:

```yaml
run_id: bt-2026xxxx-xxxx
git_commit: <sha>
config_hash: <sha256>
strategy: {id: S1, version: 1.0.0, params_hash: <sha256>}
data_snapshot_hash: <sha256>
ai_replay_store_hash: <sha256 or null>
period: {start: ..., end: ..., holdout_used: false}
cost_profile: webull_jp
random_seed: 12345          # for partial-fill sampling if enabled
variants_tried_so_far: 7
```

Re-running with the same manifest must produce byte-identical metrics (tested in CI on fixture data).

## 8. Interpreting results honestly

- Backtest and paper results are estimates under assumptions. They do **not** show that real-money trading will reproduce them.
- A strategy that only works under the cheapest cost profile, or only with one parameter value, is rejected.
- Results are reported with the number of variants tried and with confidence intervals where possible.
- A "no-go" outcome (no strategy passes) is a valid result; the system then stays in research/paper.
