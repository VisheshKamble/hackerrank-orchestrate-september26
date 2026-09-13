"""
Unit tests for ledger/build_ledger.py::detect_recurring_from_history and
RawEvent.effective_date (settlement_date fallback).

Run with:
    python3 -m pytest code/tests/test_recurrence_detection.py -v
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.build_ledger import detect_recurring_from_history
from ledger.conflict_resolution import RawEvent

AS_OF = dt.date(2026, 9, 12)


def _ev(event_id, days_ago, amount, category, flexible=True, status="settled", settlement_offset=None):
    event_date = AS_OF - dt.timedelta(days=days_ago)
    settlement_date = event_date + dt.timedelta(days=settlement_offset) if settlement_offset is not None else None
    return RawEvent(
        event_id=event_id,
        user_id="u1",
        event_date=event_date,
        settlement_date=settlement_date,
        amount=amount,
        status=status,
        is_recurring=False,
        recurrence_frequency_days=None,
        is_flexible=flexible,
        category=category,
    )


def test_effective_date_prefers_settlement_date():
    e = _ev("e1", days_ago=10, amount=-50, category="subscriptions", settlement_offset=3)
    assert e.effective_date == e.event_date + dt.timedelta(days=3)


def test_effective_date_falls_back_to_event_date():
    e = _ev("e1", days_ago=10, amount=-50, category="subscriptions")
    assert e.effective_date == e.event_date


def test_detects_regular_monthly_pattern():
    events = [
        _ev("e1", days_ago=90, amount=-50, category="subscriptions"),
        _ev("e2", days_ago=60, amount=-50, category="subscriptions"),
        _ev("e3", days_ago=30, amount=-50, category="subscriptions"),
        _ev("e4", days_ago=2, amount=-900, category="housing", flexible=False),  # unrelated, one-off-looking
    ]
    series = detect_recurring_from_history(events, AS_OF)
    assert len(series) == 1
    s = series[0]
    assert s.category == "subscriptions"
    assert s.frequency_days == 30
    assert s.signed_amount == -50
    assert s.flexible is True
    assert s.inferred is True


def test_conservative_amount_is_the_worst_case_expense():
    events = [
        _ev("e1", days_ago=90, amount=-40, category="groceries"),
        _ev("e2", days_ago=60, amount=-70, category="groceries"),  # worst case
        _ev("e3", days_ago=30, amount=-55, category="groceries"),
    ]
    series = detect_recurring_from_history(events, AS_OF)
    assert series[0].signed_amount == -70  # largest expense magnitude, not the average


def test_too_few_occurrences_not_treated_as_recurring():
    events = [
        _ev("e1", days_ago=60, amount=-50, category="subscriptions"),
        _ev("e2", days_ago=30, amount=-50, category="subscriptions"),
    ]
    assert detect_recurring_from_history(events, AS_OF) == []


def test_irregular_gaps_not_treated_as_recurring():
    events = [
        _ev("e1", days_ago=200, amount=-50, category="misc"),
        _ev("e2", days_ago=190, amount=-50, category="misc"),  # gap 10
        _ev("e3", days_ago=5, amount=-50, category="misc"),  # gap 185 — way over 2x the first gap
    ]
    assert detect_recurring_from_history(events, AS_OF) == []


def test_future_occurrence_already_present_skips_projection():
    events = [
        _ev("e1", days_ago=90, amount=-50, category="subscriptions"),
        _ev("e2", days_ago=60, amount=-50, category="subscriptions"),
        _ev("e3", days_ago=30, amount=-50, category="subscriptions"),
        _ev("e4", days_ago=-15, amount=-50, category="subscriptions"),  # already-known future row
    ]
    # Don't synthesize a projection on top of an explicitly-known future row.
    assert detect_recurring_from_history(events, AS_OF) == []


def test_disagreeing_flexibility_defaults_to_protected():
    events = [
        _ev("e1", days_ago=90, amount=-50, category="mixed", flexible=True),
        _ev("e2", days_ago=60, amount=-50, category="mixed", flexible=False),
        _ev("e3", days_ago=30, amount=-50, category="mixed", flexible=True),
    ]
    series = detect_recurring_from_history(events, AS_OF)
    assert series[0].flexible is False
