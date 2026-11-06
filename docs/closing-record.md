# Closing record (2026-11-06)

The owner ended the AI-Trader project on 2026-11-06. This repository is frozen: no further
cycles, tasks or deployments. It never traded real money; LIVE was never enabled.

## Why it ended

Owner decision in the project thread: for this owner, buying and holding a broad index fund
(S&P 500 or a global "all-country" fund) is the better choice for return, risk and effort.

The evidence behind the decision:

- No strategy passed its declared research criteria. S1 (and all four S1 v1.1 families) and S2
  were no-go against SPY (`docs/research/2026-10-24-p2-walkforward.md`,
  `docs/research/2026-10-24-s1-v1.1-results.md`). S3 failed the deflated Sharpe test
  (0.58 < 0.80) and S3-R1 also failed the drawdown limit (`docs/research/2026-10-24-s3-results.md`).
- After fees and trading costs, few retail automated strategies beat a broad index over the long
  run. Nine PAPER sessions are far too short to show skill either way.

## Final PAPER results (virtual money, modelled fills)

Both books started at the first session on or after 2026-10-26 and ran on the Cloud Run job.
Last session 2026-11-05 (9 sessions), status `as_of` 2026-11-05T23:0x UTC.

| Book | Strategy | Equity (USD) | Return | Max drawdown | SPY (B0) return | Positions at end |
|---|---|---|---|---|---|---|
| `paper` | S1 1.0.0 (reference) | 320.06 | -0.91% | 0.91% | -3.89% | XLV 0.15007 |
| `paper-s3` | S3 (observation) | 319.17 | -1.19% | 1.33% | -3.89% | IWM 0.10254, SHY 0.98951 |

The final status files and every daily report (JSON and Markdown) are archived in
`reports/paper-final/`. They were read with the reports-only reader account before the Google
Cloud project was deleted. The state archive (`state/paper-state.tar.gz`) and market data were not
copied; they could not be read with that account and are not needed.

## Shut-down steps

| Step | State |
|---|---|
| Windows task "AI-Trader PAPER daily" | Disabled since 2026-10-31 (not deleted) |
| Claude daily check (scheduled task, 08:23 JST) | Disabled 2026-11-06 |
| Final reports archived | Done 2026-11-06 (`reports/paper-final/`) |
| Google Cloud project `aitrader-paper` | Deletion by the owner 2026-11-06. Google keeps a deleted project for about 30 days before it is removed for good; it can be restored in that window. Deleting it removes the Cloud Run job, Cloud Scheduler, the bucket, Secret Manager and the reader service account with its key |
| Reader key in the cloud environment (`AITRADER_GCP_READER_KEY`) | To be removed from the environment settings; it stops working when the project is deleted |
| Alpaca data key, Webull sandbox keys | Owner revokes them in the provider dashboards and removes the Windows user environment variables |

## If the project is ever restarted

Start from `docs/status.md` and this record. The Google Cloud resources must be rebuilt from
`docs/dev/cloud-run.md` (new project, new secrets, new reader key); the PAPER books would restart
from zero.
