# TASK-010: Reconciliation of the engine's books against the broker

Implementer: Claude (owner instruction 2026-10-23). Design sources: documents 03 (§2
`reconcile`, §6 `reconciliations`), 05 (§7 SAFE → RUNNING only after a clean reconciliation,
§10 crash recovery, ORD-ERR-4). No external service.

Status: **done** (2026-10-24). 100% line and branch coverage of `reconcile`; all checks green.

## Scope

1. **Comparison** (pure): positions (exact for whole shares, within 0.000001 for fractional
   quantities), cash (settled USD, unsettled USD, JPY; within the larger of USD 0.05 and 0.1% of
   the broker's value; scope FULL only) and open orders (an open broker order the engine does not
   know; an engine order the broker does not show as open; a quantity that differs).
2. **Outcome:** `CLEAN`, `MISMATCH` or `INCOMPLETE` (orders still UNKNOWN inside the lookup
   window, so no conclusion yet).
3. **Reconciler:** first the order manager's sync (resolve PENDING_SUBMIT and UNKNOWN orders,
   book missing fills), then the comparison, then the halt state: a clean result takes SAFE to
   RUNNING, a mismatch halts (HALTED, owner reset needed), an incomplete one changes nothing.
   **Nothing is corrected automatically.**
4. **Storage:** schema version 2 adds the `reconciliations` table (ts, scope, status,
   differences); reports can be read back.

## Out of scope

The schedule (after reconnects, every 15 minutes in market hours, end of day) belongs to the
PAPER runner; alerts belong to `notify`.

## Acceptance criteria

- Each difference kind is detected, and values inside the tolerances are not reported.
- After normal trading with the SimBroker the result is CLEAN; an order placed at the broker
  outside the engine, extra cash at the broker or a missing position give MISMATCH and HALTED.
- A database created with schema 1 is migrated to schema 2.
- ruff, mypy (strict), lint-imports and pytest green; coverage ≥ 90% for `reconcile`.

## Implementation notes

- **Cash tolerance:** document 05 §10 says "USD 0.05 or 0.1%"; the implementation uses the
  larger of the two (and JPY 5 for yen cash), so rounding differences at this account size never
  halt the system while real differences (a missing deposit, an unbooked fee) do.
- **Broker unavailable** during the comparison gives INCOMPLETE, not MISMATCH: no conclusion
  can be drawn, and the state stays where it is.
- A clean result only takes SAFE to RUNNING. Leaving DEGRADED or HALTED stays with the rules of
  the halt state machine (condition cleared, or the owner's reset).
