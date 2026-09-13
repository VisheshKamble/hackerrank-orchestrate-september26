"""
earliest_date_for_full_payment: the first date within the 90-day forecast
window where paying the FULL requested_amount as a single payment, on that
date, keeps the balance safe for the remainder of the window — computed
without any optional spending changes.

Same suffix-minimum trick as amount_safe_to_pay, evaluated at every offset
instead of just offset 0.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from forecast.simulator import suffix_min


def compute_earliest_date_for_full_payment(
    baseline_series,
    start_date: dt.date,
    minimum_balance_to_keep: float,
    requested_amount: float,
) -> Optional[dt.date]:
    sm = suffix_min(baseline_series)
    for i, floor_balance in enumerate(sm):
        if floor_balance - minimum_balance_to_keep >= requested_amount - 1e-9:
            return start_date + dt.timedelta(days=i)
    return None  # not safe anywhere within the forecast horizon
