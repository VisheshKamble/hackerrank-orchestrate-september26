"""
Unit tests for ledger/conflict_resolution.py — verifies the exact priority
order from problem_statement.md:
  1. explicit cancellation/settlement/amendment
  2. newer record from the same source
  3. settled over estimate/forecast
  4. financially safer interpretation as the final tie-break

Run with:
    python3 -m pytest code/tests/test_conflict_resolution.py -v
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.conflict_resolution import RawEvent, resolve_chain, resolve_events

D1 = dt.date(2026, 1, 1)
D2 = dt.date(2026, 1, 10)


def _ev(**kwargs) -> RawEvent:
    defaults = dict(
        user_id="u1",
        event_date=D1,
        amount=-100.0,
        status="pending",
        is_recurring=False,
        recurrence_frequency_days=None,
        is_flexible=False,
        category="expense",
        linked_event_id=None,
        source_priority=0,
    )
    defaults.update(kwargs)
    return RawEvent(**defaults)


def test_cancelled_event_is_dropped():
    ev = _ev(event_id="e1", status="cancelled")
    assert resolve_chain([ev]) is None


def test_settled_beats_estimate():
    estimate = _ev(event_id="e1", status="estimate", amount=-90.0)
    settled = _ev(event_id="e1", status="settled", amount=-100.0)
    winner = resolve_chain([estimate, settled])
    assert winner.status == "settled"
    assert winner.amount == -100.0


def test_newer_source_priority_wins_over_older_pending():
    original = _ev(event_id="e1", status="pending", amount=-100.0, source_priority=0)
    amended = _ev(event_id="e1", status="amended", amount=-150.0, source_priority=1)
    winner = resolve_chain([original, amended])
    assert winner.amount == -150.0


def test_safer_interpretation_tiebreak_for_expenses_picks_larger_magnitude():
    a = _ev(event_id="e1", status="pending", amount=-100.0)
    b = _ev(event_id="e1", status="pending", amount=-200.0)
    winner = resolve_chain([a, b])
    assert winner.amount == -200.0  # larger expense = more conservative assumption


def test_safer_interpretation_tiebreak_for_income_picks_smaller_magnitude():
    a = _ev(event_id="e1", status="pending", amount=300.0)
    b = _ev(event_id="e1", status="pending", amount=150.0)
    winner = resolve_chain([a, b])
    assert winner.amount == 150.0  # smaller income = more conservative assumption


def test_lifecycle_chain_via_linked_event_id_collapses_to_one_winner():
    pending = _ev(event_id="e1", status="pending", amount=-500.0)
    settled = _ev(event_id="e2", status="settled", amount=-480.0, linked_event_id="e1")
    resolved = resolve_events([pending, settled])
    assert len(resolved) == 1
    assert resolved[0].status == "settled"
    assert resolved[0].amount == -480.0


def test_unrelated_events_all_survive():
    e1 = _ev(event_id="e1", status="confirmed")
    e2 = _ev(event_id="e2", status="confirmed")
    resolved = resolve_events([e1, e2])
    assert {e.event_id for e in resolved} == {"e1", "e2"}
