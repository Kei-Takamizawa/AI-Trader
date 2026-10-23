# 09. Checklist Before Moving to Real Money

LIVE stays disabled until **every** item is checked and the owner signs the approval record. Each item needs evidence (link to a report, CI run, document or screenshot) recorded in the release manifest. If any economic or technical item fails, the system **continues in PAPER**.

## A. Owner approval

- [ ] A1. Owner has read the latest paper report, the A/B report (if AI is used) and this checklist.
- [ ] A2. Owner approves in writing: broker, account (masked ID), live limits (hash), start date, approval expiry.
- [ ] A3. Owner confirms that the live capital is money they can afford to lose completely.
- [ ] A4. Owner confirms the incident contacts and notification channels.

## B. Broker and account

- [ ] B1. Broker is registered in Japan and legally usable by the owner.
- [ ] B2. Written confirmation of every item in document 02, section 6 (US stock API scope, fractional orders, stop orders, FX cost, tax account type, API terms for cloud use, token expiry, paper environment, data rights, settlement, outage procedure).
- [ ] B3. Account is a **cash** account (no margin, no options permission).
- [ ] B4. Owner can log in to the broker app to cancel orders manually (runbook tested).
- [ ] B5. Broker's minimum order size, quantity increment and fee schedule are entered in config and match a test order in the broker's test environment.

## C. Credentials and environment

- [ ] C1. Live API credentials created **only after A2**, stored only in the live secret store.
- [ ] C2. Live runs in its own cloud project, service account and VM; paper hosts have no live secrets.
- [ ] C3. Endpoint allow-list and account-ID hash configured; LiveGuard G4 tested with a wrong account (must refuse).
- [ ] C4. No inbound internet access to the live VM; SSH only through the private tunnel; MFA on the cloud and broker accounts.
- [ ] C5. Backups encrypted and restore tested.

## D. Code and automated tests

- [ ] D1. The release commit is tagged; CI is green for the full suite (unit, property, integration, fault).
- [ ] D2. Import rules pass (risk/execution/broker do not import AI).
- [ ] D3. Parity test passes (PAPER_SIM day replayed in BACKTEST gives identical intents).
- [ ] D4. The live broker adapter passes its integration tests against the broker's test environment.
- [ ] D5. No open critical or high review findings.

## E. Fault tests (performed on the PAPER_BROKER and pre-live environments)

- [ ] E1. Process killed between write-ahead record and broker call → recovery finds or rules out the order; no duplicate.
- [ ] E2. Process killed with open orders and positions → SAFE mode reconciles correctly.
- [ ] E3. Network cut for 2 and 15 minutes → DEGRADED, then HALTED; correct alerts; correct recovery.
- [ ] E4. Stale and missing data → no new entries.
- [ ] E5. Broker returns errors / malformed data → typed errors, DEGRADED, no crash loop.
- [ ] E6. Kill switch from CLI, from the database flag and from the remote flag → entry orders cancelled within 60 s.
- [ ] E7. Watchdog alert when heartbeat stops (VM stopped) → received on both channels.
- [ ] E8. Clock drift simulation → refuses to trade.
- [ ] E9. Restore from backup on a fresh VM → state matches broker after reconciliation.

## F. Risk configuration

- [ ] F1. Live limits file within hard caps: max order ≤ ¥30,000 equivalent (hard cap ¥50,000), total holdings ≤ ¥100,000, per-trade risk ≤ 0.5% including fees, daily 1.5%, monthly 5%, drawdown 10%.
- [ ] F2. Allow-list of instruments reviewed (liquid ETFs first).
- [ ] F3. Monthly-halt resumption policy set (Q5).
- [ ] F4. Stop-order handling for the broker confirmed (broker-side or system-side) and tested.

## G. Economic feasibility (small capital)

- [ ] G1. With the broker's real fee schedule, at least 90% of paper intents of the last 3 months pass R-ECON-1 (round-trip cost ≤ 20% of risk budget) at the planned live capital.
- [ ] G2. Fractional orders available through the API, **or** the allow-list contains enough instruments priced below the maximum position size.
- [ ] G3. Paper results recomputed with the live broker's fees and FX costs are reported (gross, fees, net).
- [ ] G4. Monthly operating cost for LIVE is estimated and the owner accepts that net results after costs may be negative (document 08).

## H. Paper evidence (not a promise of live results)

- [ ] H1. ≥ 3 months of PAPER (sim + broker) with complete daily reports.
- [ ] H2. No unexplained reconciliation differences in the last 20 trading days.
- [ ] H3. Realised slippage and fees within 1.5× of the backtest assumptions.
- [ ] H4. Paper performance compared with the backtest expectation and the benchmark; deviations explained.
- [ ] H5. No risk-rule breach that the engine failed to catch.
- [ ] H6. Report states explicitly that paper results may not repeat with real money (fills, liquidity, behaviour of real orders).

## I. Operations

- [ ] I1. Runbooks exist for: broker outage, data outage, VM down, reconciliation mismatch, unexpected position, kill switch, restore.
- [ ] I2. Deploys only outside market hours; rollback tested.
- [ ] I3. Daily and monthly reports reach the owner automatically.
- [ ] I4. Tax record export (trades, dividends, FX) produced and checked once.

## J. Staged roll-out

| Stage | Max order | Max holdings | Minimum duration | Advance only if |
|---|---|---|---|---|
| LIVE_MICRO-1 | ¥10,000 | ¥30,000 | 4 weeks | No incidents; reconciliation clean |
| LIVE_MICRO-2 | ¥20,000 | ¥50,000 | 4 weeks | Same + costs as expected |
| LIVE_MICRO-3 | ¥30,000 | ¥100,000 | 8 weeks | Same + owner approval |
| Beyond ¥100,000 | — | — | — | New approval, new limits review; hard caps changed by code review |

## K. Automatic roll-back to PAPER

Return to PAPER (and alert) if any of these happen in LIVE:

- Reconciliation mismatch that is not explained within one trading day.
- A hard-cap violation is detected (should be impossible; treated as a critical bug).
- Monthly loss limit or drawdown limit reached.
- Fees or slippage above 2× the assumption for 10 consecutive trades.
- Broker API terms or fees change in a way that fails section G.
