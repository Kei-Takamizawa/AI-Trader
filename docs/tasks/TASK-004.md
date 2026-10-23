# TASK-004: Order manager, order gate, halt state machine, SimBroker, fault tests

Implementer: Claude (owner instruction 2026-10-23). Design sources: documents 03 (§4 ports, §5
order state machine, §8 error handling), 04 (§4 composition), 05 (§6 duplicates, §7 system
states, §8 connectivity, §13 hard caps, §16.3 order errors), 12 (§4 fill models).

Protected paths touched: `src/aitrader/execution/gate.py` (owner approval needed before merge).

Status: **done** (2026-10-23). 100% line and branch coverage of `execution`, `broker` and
`risk/halt.py`; ruff, mypy (strict) and lint-imports (7 contracts) green.

## Scope

1. **Domain:** `OrderRequest`, the order sent to a broker for one attempt of one intent.
2. **Broker port** (`aitrader.broker.port`): `BrokerPort` protocol, capabilities, account,
   position, order and fill snapshots, and typed broker errors (unavailable/timeout, rejected with
   a code, not found, rate-limited, authentication).
3. **`SimBroker`** (`aitrader.broker.sim`): the broker for BACKTEST and PAPER. It keeps its own
   books in a `Ledger` (so cash-account rules and T+1 settlement match the engine), fills
   marketable limit and market orders from quotes (no price improvement beyond the limit,
   slippage in basis points, optional partial fills), charges `webull_jp` fees, is idempotent on
   `client_order_id`, and can inject faults: timeout before or after the order is accepted,
   rejection with a code, rate limit, authentication failure, duplicate fill events.
4. **Order store** (`aitrader.execution.store`): order records and an append-only event log;
   one active order per intent. In-memory implementation; the SQLite one comes with the storage
   task before PAPER.
5. **Order state machine** (`aitrader.execution.states`): the transitions of document 03 §5.
6. **Order gate** (`aitrader.execution.gate`): HC-1 to HC-11 checked immediately before every
   broker call, using the smaller of the configured limit and the hard cap.
7. **Order manager** (`aitrader.execution.manager`): write-ahead record before the broker call;
   timeout → `UNKNOWN` and lookup by `client_order_id`, never a blind resubmission; `NOT_FOUND`
   after a reconciliation window; at most one explicit retry after `NOT_FOUND` (DUP-2); fills
   booked in the engine ledger exactly once, and fills that do not match the order record are
   not booked but reported (ORD-ERR-4); cancels; crash recovery of `PENDING_SUBMIT` and
   `UNKNOWN` records.
8. **Halt state machine** (`aitrader.risk.halt`): SAFE, RUNNING, DEGRADED, HALTED, KILLED with
   the transitions of document 05 §7, who may clear which halt, and a history that survives
   restarts. **Order-error monitor** (`aitrader.execution.monitor`): ORD-ERR-1 to ORD-ERR-5 and
   AUTH-4 drive the state machine.

## Out of scope

SQLite persistence, the Webull adapter (TASK-008), bar-based fill models with gaps and
participation limits (TASK-005), protective system-side stops and the kill-switch command
(runner, Phase 3), AUTH-1..3, CONN-* and DATA-* rules that need a live adapter.

## Acceptance criteria

- Every allowed and every refused order-state transition is tested.
- Every gate rule passes and fails in a test; an order that passes the gate never breaks a hard
  cap (property test).
- Fault tests: crash between the write-ahead record and the broker call (before and after the
  broker accepted the order); broker timeout after accepting an order; duplicate fill events;
  fill that does not match the order; kill switch during an open order; authentication failure;
  a run of order rejections (ORD-ERR-1 → ORD-ERR-2).
- ruff, mypy (strict), lint-imports and pytest green; coverage ≥ 90% for `execution`, `broker`
  and `risk`.

## Implementation notes

- **HC-6** does not block protective exits (stop exits, flatten); document 05 §13 now says so.
- **DUP-2 and document 03 §5:** an UNKNOWN order is never resubmitted automatically. One retry is
  possible only through an explicit `retry_not_found()` call after the broker confirmed the first
  attempt absent; any other earlier order (rejected, filled, cancelled) means the intent is done.
- **Rejections before processing** (rate limit, authentication) mark the order REJECTED with the
  event ORD-ERR-5 or AUTH-4; they do not count towards the ORD-ERR-2 rejection run.
- **Re-delivered fills** (the fill cursor is inclusive, and event streams deliver at least once)
  are skipped silently because the ledger already holds them.
- **Kill switch:** `cancel_non_protective()` cancels open entries and ordinary exits (document 05
  §11); protective stop exits stay, and flatten orders still pass the gate in KILLED.
- **Halt states:** RUNNING and DEGRADED fall back to SAFE only at a process start; the daily
  loss halt is the only one the system clears itself.
- The SimBroker uses weekday-only T+1 settlement until the exchange calendar arrives (TASK-005).
