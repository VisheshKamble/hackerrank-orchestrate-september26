"""
Deterministic conflict resolution across financial_events.csv rows that
describe the same underlying thing (linked via linked_event_id chains, or
amended/cancelled by a later message).

Implements, in order, exactly the priority rules from problem_statement.md:
  1. An explicit cancellation, settlement, or amendment
  2. A newer record from the same source
  3. A settled event over an estimate or forecast
  4. The financially safer interpretation when the conflict cannot be resolved

This module contains no LLM calls and no I/O — it is a pure function over
already-structured rows, specifically so it is easy to unit test and easy
to defend in review ("why is this number what it is").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Status strings we recognize, ranked best (most authoritative) to worst.
# "cancelled" / "failed" / "duplicate" are handled separately (dropped
# entirely, never just de-prioritized).
_STATUS_RANK = {
    "settled": 0,
    "confirmed": 0,
    "amended": 1,
    "pending": 2,
    "estimate": 3,
    "forecast": 3,
}
_DROP_STATUSES = {"cancelled", "canceled", "failed", "duplicate", "reversed"}


@dataclass
class RawEvent:
    event_id: str
    user_id: str
    event_date: object  # datetime.date — when the record was created/initiated; used for conflict-recency only
    amount: float  # signed, home-currency, already resolved from images if it was blank
    status: str
    is_recurring: bool
    recurrence_frequency_days: Optional[int]
    is_flexible: bool
    category: str
    linked_event_id: Optional[str] = None
    source_priority: int = 0  # higher = newer/more authoritative source, e.g. a later message
    settlement_date: object = None  # datetime.date or None — when cash actually moves; None => falls back to event_date

    @property
    def effective_date(self):
        """The date to use for CASH-FLOW TIMING (forecast/simulator.py),
        as opposed to event_date, which conflict_resolution uses for
        recency tie-breaking. Falls back to event_date when settlement_date
        wasn't supplied — most one-off events settle the same day they're
        recorded, so this is a safe default, not a guess."""
        return self.settlement_date if self.settlement_date is not None else self.event_date


def _is_dropped(ev: RawEvent) -> bool:
    return (ev.status or "").strip().lower() in _DROP_STATUSES


def _status_rank(ev: RawEvent) -> int:
    return _STATUS_RANK.get((ev.status or "").strip().lower(), 2)  # unknown -> treat as "pending"


def _safer_expense_first(ev: RawEvent) -> tuple:
    """
    Rule 4 tie-break: 'the financially safer interpretation'. For an
    expense (negative amount) safer = larger magnitude (assume the worse
    case). For income (positive amount) safer = smaller magnitude (don't
    assume money that might not show up). This sort key picks the
    conservative event first.
    """
    if ev.amount < 0:
        return (-abs(ev.amount),)  # most negative (largest expense) sorts first
    return (abs(ev.amount),)  # smallest income sorts first


def group_by_lifecycle(events: List[RawEvent]) -> Dict[str, List[RawEvent]]:
    """Group events into lifecycle chains via linked_event_id.
    Each chain's key is its root event_id (the event with no linked_event_id,
    or the earliest event_id found if the chain is circular/incomplete)."""
    by_id = {e.event_id: e for e in events}
    root_of: Dict[str, str] = {}

    def find_root(eid: str, seen=None) -> str:
        seen = seen or set()
        if eid in seen:
            return eid  # guard against malformed cycles
        seen.add(eid)
        ev = by_id.get(eid)
        if ev is None or not ev.linked_event_id or ev.linked_event_id not in by_id:
            return eid
        return find_root(ev.linked_event_id, seen)

    chains: Dict[str, List[RawEvent]] = {}
    for e in events:
        root = root_of.setdefault(e.event_id, find_root(e.event_id))
        chains.setdefault(root, []).append(e)
    return chains


def resolve_chain(chain: List[RawEvent]) -> Optional[RawEvent]:
    """Pick the single winning event for one lifecycle chain, or None if the
    whole chain resolves to 'dropped' (e.g. cancelled)."""
    survivors = [e for e in chain if not _is_dropped(e)]
    if not survivors:
        return None
    if len(survivors) == 1:
        return survivors[0]

    # Rule 1: explicit amendment/settlement outranks a plain pending/estimate.
    # Rule 2: newer record from the same source (higher source_priority, then
    #         later event_date) wins among equals.
    # Rule 3: settled over estimate/forecast — folded into _status_rank.
    survivors.sort(
        key=lambda e: (
            _status_rank(e),          # lower is better (settled/amended first)
            -e.source_priority,       # higher priority first
            -_date_ordinal(e.event_date),  # newer date first
            _safer_expense_first(e),  # rule 4 final tie-break
        )
    )
    return survivors[0]


def _date_ordinal(d) -> int:
    return d.toordinal() if hasattr(d, "toordinal") else 0


def resolve_events(events: List[RawEvent]) -> List[RawEvent]:
    """Full resolution pass: group by lifecycle, resolve each chain, drop
    cancelled/failed/duplicate entirely, return the clean surviving set."""
    chains = group_by_lifecycle(events)
    resolved = []
    for chain in chains.values():
        winner = resolve_chain(chain)
        if winner is not None:
            resolved.append(winner)
    return resolved
