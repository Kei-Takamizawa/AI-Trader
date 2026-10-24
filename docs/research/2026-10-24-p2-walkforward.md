# P2 research report: walk-forward results for S1 and S2 (TASK-007c)

Date: 2026-10-24. Author: Claude (designer). Runs made by the operator on the owner's Mac.
Status: **draft for the owner's decision**. These are backtest estimates under the
assumptions listed below; they do not show that real-money trading would reproduce them
(document 12 §8).

## 1. What was run

| Item | Value |
|---|---|
| Code | commit `7892751` (TASK-007b) |
| Data | Alpaca daily bars for universe U0 (23 ETFs), 2016-01-04 to 2026-10-16; raw `f9469b49…5a5815`, adjusted `8985aaec…5a5c52` |
| Protocol | Walk-forward, 36-month training windows and 6-month test windows, starting 2017-01-03 (document 12 §3) |
| Selection | Highest training Sharpe among the pre-declared grid; reported on the test windows only |
| Test period | 9 folds, chained: 2020-01-03 to 2024-07-02 (4.5 years) |
| Hold-out | Locked from 2024-10-17 (24 months); **not used** |
| Not covered | 2024-07-03 to 2024-10-16 (between the last test window and the hold-out; no truncation by design) |
| Capital and costs | USD 323 (about JPY 50,000), `webull_jp` cost profile, 1 bp half-spread, risk per trade 0.5% of equity, dividends after 10% US and 20.315% Japanese withholding (assumed) |
| Variants tried | S1: 4 (top 3 or 2, skip 0 or 5 days); S2: 1 |
| Wall time | S1 280 s, S2 102 s |

## 2. Results on the chained test windows

| Measure | S1 (momentum rotation) | S2 (pullback) | B0 (SPY buy-and-hold) |
|---|---|---|---|
| Total return | +5.15% | +1.75% | +77.06% |
| CAGR | 1.13% | 0.39% | about 13.5% |
| Average month (geometric) | about 0.09% | about 0.03% | about 1.06% |
| Months ≥ 1% / ≥ 2% | 20.0% / 1.8% | 3.6% / 0% | — |
| Max drawdown | 6.10% (573 sessions) | 2.83% (706 sessions) | 33.88% |
| Sharpe (rf = 0) | 0.30 | 0.24 | not computed by the runner |
| Return / max drawdown | 0.18 | 0.14 | about 0.40 |

Yearly: S1 2020 +1.79%, 2021 +0.70%, 2022 −0.82%, 2023 +0.69%, 2024 (H1) +2.73%.
S2 2020 −0.91%, 2021 +3.10%, 2022 −0.70%, 2023 −0.94%, 2024 (H1) +1.26%.

Per fold (test windows): S1 was positive in 6 of 9, with profit factors below 1 in 7 of 9 folds;
S2 was positive in 4 of 9 and did not trade at all in the second half of 2022.

## 3. Stability of the selection (S1)

- The selected variant changed four times in nine folds (skip 5/top 3, then skip 5/top 2,
  then skip 0/top 2, then back). Training Sharpes were below 0.45 for every variant in every
  fold and negative for at least one variant in eight folds.
- The default variant (skip 0, top 3) had a negative training Sharpe in six of nine folds.
- There is no stable optimum: neighbouring variants differ in sign from fold to fold. This is the
  "knife-edge" pattern document 12 §3 warns about, at a low level of performance.

## 4. Why the returns are low (diagnosis, not yet tested)

1. **Low exposure by construction.** The risk engine sizes each entry so that a stop-out costs
   0.5% of equity. With a 3 × ATR(20) stop (typically 3–5% for these ETFs), one position is
   about 10–17% of equity, so three positions hold roughly 30–50%. S2 holds broad ETFs for a
   few days at a time and is mostly in cash. Both strategies therefore earn a fraction of the
   market's return, while also avoiding most of its drawdown.
2. **Weak edge after costs.** Profit factors below 1 in most S1 folds mean the selection itself
   added little; the positive total comes from a few strong windows (2020 H2, 2024 H1).
3. **Small capital.** At USD 323, per-share regulatory minimums and cent rounding are a larger
   share of each trade than they would be with more capital (not separately measured).

## 5. Assessment against the targets

- The 1–2% monthly target is **not met** by either strategy (about 0.09% and 0.03% per month).
  For reference, buy-and-hold SPY averaged about 1.06% per month over the same period, with a
  34% drawdown; a 1–2% monthly target with low drawdowns is above what this period's market
  offered.
- Neither strategy beats B0 on return per unit of drawdown.

## 6. Go / no-go (document 07, P2 exit)

| Strategy | Decision | Reason |
|---|---|---|
| S1 1.0.0 | **No-go for LIVE.** Usable as a reference strategy for paper-trading infrastructure tests. | Positive but small return, unstable selection, worse return/drawdown than B0. |
| S2 1.0.0 | **No-go.** | Near-zero return, long idle periods, unstable across folds; consistent with the documented decay noted in document 11 §3. |

The hold-out stays locked: no strategy version is frozen for a final test.

## 7. Options for the next phase (owner's decision)

1. **Build the paper-trading path (P3) with S1 as the reference strategy**, without real money,
   while research continues. This tests the operational system (Webull sandbox adapter, daily
   cycle, reconciliation, monitoring) independently of strategy quality.
2. **Research S1 version 1.1** with changes declared before any run, each counted as a variant:
   for example a cash proxy (BIL) when nothing qualifies, a higher risk per trade within the
   hard caps (needs the owner's approval), or volatility-targeted sizing. Each change must be
   justified before testing, not chosen from results.
3. **Pause** strategy work and keep the system in research.

## 8. Limitations

- One data vendor (Alpaca SIP daily bars); dividends are estimated from the raw and adjusted
  series, and withholding rates are assumptions.
- No Sharpe for B0 and no deflated Sharpe or bootstrap intervals yet (planned, document 12 §6).
- Fills are modelled at the open with a fixed spread and the cost model of document 12 §4; no
  live fills exist to calibrate them.
- 4.5 years of test data with a few trades per month is a small sample.
