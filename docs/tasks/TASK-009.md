# TASK-009: SQLite storage (orders, journal, halts, decisions, runs, single instance)

Implementer: Claude (owner instruction 2026-10-23). Design sources: documents 03 (§6 data model,
§8 error handling), 05 (§6 DUP-1 and DUP-5, §7 halts survive restarts, §10 crash recovery,
§15 fault tests: "database locked"). Needed before PAPER (Phase 3); no external service.

Status: **done** (2026-10-24). 843 tests, 100% line and branch coverage; the order-store tests run
against both the in-memory and the SQLite store.

## Scope

1. **Database** (`aitrader.storage.Database`): one SQLite file per mode and account; schema
   migrations with a version table; WAL journal, foreign keys, a busy timeout; an integrity
   check when opened; online backup to a file. Errors are typed: `StorageBusyError` (database
   locked), `StorageIntegrityError`, `StorageError`.
2. **Order store** (`SqliteOrderStore`): the `OrderStore` port on SQLite. A partial unique index
   allows only one active order per intent (DUP-1 enforced by the database as well); every change
   appends to `order_events`.
3. **Durable ledger** (`DurableLedger`): a `Ledger` that writes each journal entry in the same
   transaction that applies it, and is rebuilt from the journal when opened.
4. **Durable halt state** (`DurableHaltStateMachine`): every transition is stored; a halt before a
   restart is still a halt after it.
5. **Decision log**: every risk decision with its intent (audit trail of document 05).
6. **Runs**: one row per process start or backtest, with mode, commit and config hash.
7. **Single instance** (DUP-5): an exclusive file lock on the database file plus a lease row per
   mode and account; a second engine refuses to start.

## Out of scope

Bars and market data (Parquet, TASK-006), operating-cost import, the PAPER runner that wires
these together (Phase 3).

## Acceptance criteria

- Orders, journal and halts survive closing and reopening the database, and the rebuilt ledger
  equals the one before the restart.
- Crash test: the process stops after the write-ahead record; a new process with a new
  connection resolves the order by lookup.
- A failed journal write leaves the ledger unchanged; a refused entry leaves the database
  unchanged.
- "Database locked" is reported as `StorageBusyError`; a second engine cannot take the lock or
  the lease.
- ruff, mypy (strict), lint-imports and pytest green; coverage ≥ 90% for `storage`.

## Implementation notes

- **Durable ledger:** the journal row is inserted and the entry applied inside one transaction.
  If the commit itself fails after the entry was applied in memory, the ledger refuses every
  further entry ("out of sync; restart"); a restart rebuilds it from what was stored.
- **Durable halt state:** a transition is validated in memory, then stored; if storing fails it
  is taken back, so a halt that is not on disk never takes effect.
- **Single instance:** `SingleInstanceLock` uses `flock` on Linux (and `msvcrt` on Windows
  development PCs); the lease row expires unless the engine renews it.
