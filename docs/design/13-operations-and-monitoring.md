# 13. Unattended Operation, Monitoring and the Muse Interface

## 1. Hosting

| Item | Design |
|---|---|
| Where | Google Cloud Compute Engine, region `us-east1` (proposed; needs owner approval Q3) |
| VM | e2-micro (free tier) for PAPER with REST-based brokers; e2-small if memory is short or a Java gateway is required |
| OS | Ubuntu LTS (or Debian), automatic security updates applied in the weekend maintenance window |
| Process | `systemd` service `aitrader@<mode>.service` with `Restart=on-failure` and restart rate limits; one instance per mode/account (lock) |
| Time | chrony/NTP enabled; drift checked at start-up (DQ-6) |
| Access | No public inbound ports; SSH via the cloud provider's identity-aware tunnel or a private network |
| Secrets | Cloud secret store, read at start-up; never written to disk or logs |
| Storage | Persistent disk for SQLite, Parquet and logs; daily backup to object storage (encrypted, 30-day retention) |
| Deploys | Only outside market hours, from a tagged release with green CI; previous release kept for one-command rollback |

The owner's Mac is not needed for trading. It is only used by the operator (Claude on the PC) for development.

## 2. Structured logs

JSON Lines, one event per line, UTC timestamps, written to the journal and to rotating files.

```json
{"ts":"2026-11-02T14:35:02.120Z","run_id":"paper-...","mode":"PAPER_SIM","level":"INFO",
 "event":"order.submitted","client_order_id":"S1-...-1","symbol":"XLK","side":"BUY",
 "qty":"0.215","limit":"231.40","intent_id":"...","risk_decision":"APPROVE","git":"abc1234"}
```

Event families: `run.*`, `heartbeat`, `data.*` (ingest, quality), `ai.*` (request, response, invalid, budget), `strategy.intent`, `risk.decision`, `order.*`, `fill`, `stop.*`, `reconcile.*`, `state.*` (halt/kill transitions), `cost.*`, `alert.*`, `error.*`.

Rules: no secrets; account IDs masked; every order-related event carries `intent_id` and `client_order_id` so one trade can be followed end to end.

## 3. Database

The SQLite tables of document 03, section 6 are the system of record. Logs are for debugging; reports and the status interface read from the database.

## 4. Read-only status interface (for the owner, Muse and other tools)

### 4.1 Status bundle (files, Phase 3)

Written by the exporter after every relevant event and at least every 5 minutes:

| File | Content |
|---|---|
| `status/latest.json` | State (SAFE/RUNNING/DEGRADED/HALTED/KILLED) and reason, last heartbeat, last data time, last reconciliation result, open orders count, positions (symbol, qty, value), equity, day and month P&L, active halts, AI overlay on/off, budget used |
| `reports/daily/YYYY-MM-DD.json` and `.md` | Trades with reasons (strategy rule + risk rules + AI influence), fills, fees, P&L (trading / operating / net), incidents, data-quality findings, AI summary |
| `reports/monthly/YYYY-MM.json` and `.md` | Metrics from document 12, cost breakdown, limit usage, comparison with backtest expectation and benchmark |
| `incidents/open.json` | Open incidents with severity and suggested runbook |

A copy of the bundle is synced to a private object-storage location so it can be read without access to the VM.

### 4.2 Read-only HTTP API (Phase 5)

Bound to the private network only, token-protected, **no write endpoints**:

| Endpoint | Returns |
|---|---|
| `GET /v1/health` | Liveness and state |
| `GET /v1/status` | Same as `status/latest.json` |
| `GET /v1/pnl/daily?from=&to=` | Daily trading P&L, operating costs, net |
| `GET /v1/trades?date=` | Trades with full reasons |
| `GET /v1/incidents?status=open` | Incidents |
| `GET /v1/logs/anomalies?since=` | Warning/error events, redacted |

### 4.3 Muse

Muse reads the bundle or the API (integration levels M1–M3 in document 06, section 6). Muse has no write path. Its notes are stored as advisory text and reviewed by Claude before any change is proposed.

## 5. Alerts

| Severity | Examples | Channels | Expected owner action |
|---|---|---|---|
| CRITICAL | Heartbeat missing; HALTED by reconciliation mismatch; unexpected position; kill switch fired; hard-cap violation detected; LIVE start refused | Email + push, repeated every 15 min until acknowledged | Check status; follow runbook; decide on manual action |
| HIGH | DEGRADED > 10 min; monthly soft limit reached; protective stop could not be placed; AI budget exhausted | Email + push once | Review same day |
| WARNING | AI output invalid; single data-quality failure; order not filled in window | Daily report | Review in the daily report |
| INFO | Daily and monthly reports | Email | Read when convenient |

Alert rate limiting prevents floods; a CRITICAL alert is never suppressed.

## 6. Independent watchdog

| Aspect | Design |
|---|---|
| Placement | Outside the trading VM: a heartbeat SaaS (free tier) or a small cloud function on a schedule |
| Inputs | Engine heartbeats (every 60 s) and the freshness of `status/latest.json` in object storage |
| Checks | WD-1 to WD-6 in document 05, section 12 |
| Actions | Send alerts on two channels; optionally set the remote kill flag (the engine polls it every 30 s) |
| Self-check | Daily "watchdog alive" message; if it does not arrive, the owner knows the watchdog itself is down |
| Independence | Does not use the AI service, the engine's database or the broker credentials |

## 7. Runbooks (to be written in Phase 3)

| Runbook | Trigger |
|---|---|
| RB-01 Broker API outage | CONN-1/2 alerts |
| RB-02 Market data outage | DQ-1 alerts |
| RB-03 VM or process down | Watchdog heartbeat alert |
| RB-04 Reconciliation mismatch | HALTED by reconcile |
| RB-05 Unexpected position or order | Reconcile diff shows unknown items |
| RB-06 Kill switch and manual cancel in the broker app | Any emergency |
| RB-07 Restore from backup on a new VM | Disk or VM loss |
| RB-08 Monthly or drawdown halt review | R-MONTH-2, R-DD-1 |
| RB-09 Credential rotation | Scheduled or suspected leak |
| RB-10 Release and rollback | Every deploy |

## 8. Routine operations

| When | What |
|---|---|
| Every trading day | Automated daily report; owner reads it when convenient |
| Weekly (weekend) | Backups verified, OS updates, log rotation, dependency audit report |
| Monthly | Monthly report; AI and cloud invoices reconciled into `operating_costs`; limit review |
| Quarterly | Restore drill; fault drill; review of broker terms and fees |
