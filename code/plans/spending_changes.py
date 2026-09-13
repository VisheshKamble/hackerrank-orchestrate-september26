"""
Generates candidate spending-change combinations (stop / reduce_to) tried
only when a plan is otherwise unsafe or would miss its deadline without
them. Only flexible recurring expenses are ever touched. At most
MAX_SPENDING_CHANGES changes, and stop/reduce on the same event are mutually
exclusive (enforced simply by construction: each event contributes at most
one action to any single combination).
"""
from __future__ import annotations

import itertools
from typing import List, Optional, Tuple

from config.settings import MAX_SPENDING_CHANGES
from ledger.build_ledger import RecurringEvent

Change = Tuple[str, str, Optional[float]]  # ("stop"|"reduce_to", event_id, new_amount|None)


def flexible_candidates(recurring: List[RecurringEvent]) -> List[RecurringEvent]:
    """Flexible expenses only (positive recurring income is never touched),
    largest expense first — biggest lever tried first keeps the search small
    and keeps the eventual plan closer to 'no changes' when a smaller change
    would already do."""
    flex = [r for r in recurring if r.flexible and r.signed_amount < 0]
    flex.sort(key=lambda r: r.signed_amount)  # most negative (largest expense) first
    return flex


def stop_action(event: RecurringEvent) -> Change:
    return ("stop", event.event_id, None)


def reduce_action(event: RecurringEvent, fraction: float = 0.5) -> Change:
    new_amount = round(abs(event.signed_amount) * fraction, 2)
    return ("reduce_to", event.event_id, new_amount)


def candidate_change_sets(recurring: List[RecurringEvent], max_changes: int = MAX_SPENDING_CHANGES):
    """Yield bounded combinations of permitted spending changes.

    The search is exhaustive over the first eight flexible expenses and up to
    ``max_changes`` distinct events. This is small enough for the 90-day
    simulator while avoiding the greedy-search blind spot where a valid pair
    of smaller changes is missed.
    """
    flex = flexible_candidates(recurring)
    if not flex:
        return
    search_events = flex[:8]
    for r in range(1, min(max_changes, len(search_events)) + 1):
        for combo in itertools.combinations(search_events, r):
            for actions in itertools.product(("reduce_to", "stop"), repeat=r):
                yield [
                    reduce_action(event) if action == "reduce_to" else stop_action(event)
                    for event, action in zip(combo, actions)
                ]
