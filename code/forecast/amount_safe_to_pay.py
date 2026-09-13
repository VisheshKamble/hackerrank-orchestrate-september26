"""
amount_safe_to_pay: the largest amount payable on request_date, BEFORE any
optional spending changes, without ever breaking the 90-day safety check.

Closed form (see simulator.py docstring for the derivation):
    amount_safe_to_pay = clamp(suffix_min[0] - minimum_balance_to_keep, 0, requested_amount)
"""
from __future__ import annotations

from forecast.simulator import suffix_min


def compute_amount_safe_to_pay(
    baseline_series, minimum_balance_to_keep: float, requested_amount: float
) -> float:
    sm = suffix_min(baseline_series)
    room = sm[0] - minimum_balance_to_keep
    amount = max(0.0, min(requested_amount, room))
    # Round to cents — CSV outputs shouldn't carry float noise.
    return round(amount, 2)
