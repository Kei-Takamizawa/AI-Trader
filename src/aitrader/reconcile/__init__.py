"""Reconciliation of the engine's books with the broker: report, then halt on any mismatch.
Never corrects anything automatically, and never imports AI."""

from aitrader.reconcile.reconciler import (
    Difference,
    DifferenceKind,
    Outcome,
    Reconciler,
    ReconciliationReport,
    Scope,
    Tolerances,
    cash_matches,
    compare_cash,
    compare_orders,
    compare_positions,
    quantities_match,
)

__all__ = [
    "Difference",
    "DifferenceKind",
    "Outcome",
    "Reconciler",
    "ReconciliationReport",
    "Scope",
    "Tolerances",
    "cash_matches",
    "compare_cash",
    "compare_orders",
    "compare_positions",
    "quantities_match",
]
