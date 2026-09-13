"""
Deterministic 90-day balance simulator.

This is the single most important module in the solution — every graded
number (amount_safe_to_pay, affordability_status, earliest_date_for_full_payment)
is derived from the balance series this module produces. It is pure
arithmetic over the Ledger, with no LLM calls, so it is exactly reproducible
and cheap to unit test.

Key trick used throughout: instead of re-simulating per candidate payment,
we compute the *baseline* balance series (no request payment applied) once,
then take a suffix-minimum. A single payment of X on day d is safe exactly
when X <= suffix_min[d] - minimum_balance_to_keep. That gives closed-form
answers for amount_safe_to_pay and earliest_date_for_full_payment instead of
a slower binary search / day-by-day retry loop.
"""
from __future__ import annotations

import datetime as dt
import calendar
from collections import defaultdict
from typing import List, Optional, Sequence, Tuple

from config.settings import FORECAST_HORIZON_DAYS
from ledger.build_ledger import Ledger, RecurringEvent


def _roll_forward_to_window(reference_date: dt.date, frequency_days: int, start_date: dt.date) -> dt.date:
    """First occurrence of a recurring event on or after start_date, given a
    known reference occurrence and a fixed cadence."""
    if frequency_days <= 0:
        return reference_date
    if 28 <= frequency_days <= 31:
        occurrence = reference_date
        while occurrence < start_date:
            month = occurrence.month + 1
            year = occurrence.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = min(reference_date.day, calendar.monthrange(year, month)[1])
            occurrence = dt.date(year, month, day)
        return occurrence
    if reference_date >= start_date:
        # Still align it to the cycle in case reference_date is far in the future.
        delta_cycles = 0
    else:
        days_short = (start_date - reference_date).days
        delta_cycles = -(-days_short // frequency_days)  # ceil division
    return reference_date + dt.timedelta(days=frequency_days * delta_cycles)


def apply_spending_changes(
    recurring: Sequence[RecurringEvent], changes: Sequence[Tuple[str, str, Optional[float]]]
) -> List[RecurringEvent]:
    """changes: list of ("stop", event_id, None) or ("reduce_to", event_id, new_amount).
    Only ever touches flexible events — call sites are expected to have
    already filtered for that, but we double-check here as a safety net."""
    changes_by_event = {c[1]: c for c in changes}
    out = []
    for r in recurring:
        change = changes_by_event.get(r.event_id)
        if change is None:
            out.append(r)
            continue
        if not r.flexible:
            out.append(r)  # never allowed to touch a non-flexible expense
            continue
        action = change[0]
        if action == "stop":
            continue  # drop it entirely
        if action == "reduce_to":
            new_amount = change[2]
            sign = -1 if r.signed_amount < 0 else 1
            out.append(
                RecurringEvent(
                    event_id=r.event_id,
                    signed_amount=sign * abs(new_amount),
                    reference_date=r.reference_date,
                    frequency_days=r.frequency_days,
                    flexible=r.flexible,
                    category=r.category,
                )
            )
    return out


def build_balance_series(
    ledger: Ledger,
    start_date: dt.date,
    days: int = FORECAST_HORIZON_DAYS,
    spending_changes: Sequence[Tuple[str, str, Optional[float]]] = (),
) -> List[float]:
    """series[i] = balance at END of day (start_date + i), after that day's
    recurring/one-time events, BEFORE any candidate payment for the request
    being evaluated."""
    recurring = apply_spending_changes(ledger.recurring_events, spending_changes)

    delta_by_day = defaultdict(float)
    for oe in ledger.one_time_events:
        offset = (oe.date - start_date).days
        if 0 <= offset < days:
            delta_by_day[offset] += oe.signed_amount

    for r in recurring:
        occ = _roll_forward_to_window(r.reference_date, r.frequency_days, start_date)
        offset = (occ - start_date).days
        while offset < days:
            if offset >= 0:
                delta_by_day[offset] += r.signed_amount
            offset += r.frequency_days

    series = []
    balance = ledger.available_balance
    for i in range(days):
        balance += delta_by_day.get(i, 0.0)
        series.append(balance)
    return series


def suffix_min(series: Sequence[float]) -> List[float]:
    n = len(series)
    out = [0.0] * n
    if n == 0:
        return out
    out[-1] = series[-1]
    for i in range(n - 2, -1, -1):
        out[i] = min(series[i], out[i + 1])
    return out


def simulate_with_payments(
    series: Sequence[float],
    payments: Sequence[Tuple[int, float]],
    minimum_balance_to_keep: float,
) -> Tuple[bool, Optional[int]]:
    """General safety check for an arbitrary set of (day_offset, amount)
    payments layered onto a baseline series (used for installment plans and
    partial-payment verification, where a single closed-form formula doesn't
    apply because there are multiple payment dates).
    Returns (is_safe, first_violating_day_offset_or_None)."""
    n = len(series)
    cumulative = [0.0] * n
    running = 0.0
    payments_by_day = defaultdict(float)
    for day, amount in payments:
        if 0 <= day < n:
            payments_by_day[day] += amount
    for i in range(n):
        running += payments_by_day.get(i, 0.0)
        cumulative[i] = running
    for i in range(n):
        if series[i] - cumulative[i] < minimum_balance_to_keep - 1e-9:
            return False, i
    return True, None
