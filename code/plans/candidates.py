"""
Generate every candidate payment plan for a request, check each for safety
(with, and only if needed, spending changes), and hand the safe+eligible
survivors to plans/ranker.py.

Each candidate is deterministic and traceable back to a rule in
problem_statement.md — nothing here is an LLM judgment call.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import List, Optional, Sequence

import pandas as pd

from forecast.simulator import build_balance_series, simulate_with_payments
from ingest.schema import CANDIDATES, resolve_column
from ledger.build_ledger import Ledger
from plans.spending_changes import Change, candidate_change_sets


@dataclasses.dataclass
class PaymentPlan:
    method: str  # full_payment | partial_payment | installments | wait
    payments: List[tuple]  # [(date, amount), ...] chronological
    deadline_met: bool
    total_paid: float
    spending_changes: List[Change]
    payment_option_id: Optional[str] = None

    @property
    def num_payments(self) -> int:
        return len(self.payments)

    @property
    def start_date(self) -> dt.date:
        return self.payments[0][0]


def _to_day_offsets(payments, start_date: dt.date):
    return [((d - start_date).days, amt) for d, amt in payments]


def _is_safe(ledger: Ledger, start_date: dt.date, payments, changes: Sequence[Change]) -> bool:
    series = build_balance_series(ledger, start_date, spending_changes=changes)
    safe, _ = simulate_with_payments(series, _to_day_offsets(payments, start_date), ledger.minimum_balance_to_keep)
    return safe


def _try_with_and_without_changes(
    ledger: Ledger, start_date: dt.date, payments, deadline_met: bool, method: str, payment_option_id=None
) -> Optional[PaymentPlan]:
    total = sum(a for _, a in payments)
    if _is_safe(ledger, start_date, payments, []):
        return PaymentPlan(method, payments, deadline_met, total, [], payment_option_id)
    for changes in candidate_change_sets(ledger.recurring_events):
        if _is_safe(ledger, start_date, payments, changes):
            return PaymentPlan(method, payments, deadline_met, total, list(changes), payment_option_id)
    return None


def full_payment_candidate(
    ledger: Ledger, request_date: dt.date, requested_amount: float, desired_completion_date: dt.date
) -> Optional[PaymentPlan]:
    # Per the 90-Day Safety Check, completing by desired_completion_date is
    # part of what makes a plan "safe" at all (wait is the sole exception —
    # see wait_candidate). request_date is virtually always <= the deadline
    # in this dataset, but we guard it explicitly rather than assume it.
    if request_date > desired_completion_date:
        return None
    return _try_with_and_without_changes(
        ledger, request_date, [(request_date, requested_amount)], True, "full_payment"
    )


def wait_candidate(
    ledger: Ledger,
    earliest_full_payment_date: Optional[dt.date],
    requested_amount: float,
    desired_completion_date: dt.date,
    request_date: dt.date,
) -> Optional[PaymentPlan]:
    if earliest_full_payment_date is None or earliest_full_payment_date == request_date:
        return None
    deadline_met = earliest_full_payment_date <= desired_completion_date
    # No spending-change search here: earliest_full_payment_date was itself
    # derived WITHOUT spending changes, so this candidate is safe by
    # construction on the base ledger.
    payments = [(earliest_full_payment_date, requested_amount)]
    if _is_safe(ledger, request_date, payments, []):
        return PaymentPlan("wait", payments, deadline_met, requested_amount, [])
    return None


def partial_payment_candidate(
    ledger: Ledger,
    request_date: dt.date,
    requested_amount: float,
    amount_safe_to_pay: float,
    earliest_full_payment_date: Optional[dt.date],
    desired_completion_date: dt.date,
    allows_partial_payment: bool,
) -> Optional[PaymentPlan]:
    if not allows_partial_payment:
        return None
    if not (0 < amount_safe_to_pay < requested_amount):
        return None
    if earliest_full_payment_date is None or earliest_full_payment_date > desired_completion_date:
        return None
    remainder = round(requested_amount - amount_safe_to_pay, 2)
    payments = [(request_date, amount_safe_to_pay), (earliest_full_payment_date, remainder)]
    deadline_met = earliest_full_payment_date <= desired_completion_date
    return _try_with_and_without_changes(ledger, request_date, payments, deadline_met, "partial_payment")


def installment_candidates(
    ledger: Ledger,
    request_date: dt.date,
    request_id: str,
    payment_options_df: pd.DataFrame,
    desired_completion_date: dt.date,
) -> List[PaymentPlan]:
    """Every row in request_payment_options.csv for this request_id becomes
    (at most) one candidate. Schedules are taken exactly as supplied —
    'Respect all supplied payment-option schedules', never re-derived."""
    if payment_options_df is None or payment_options_df.empty:
        return []
    if ledger.max_installment_months is None:
        # Blank max_installment_months means the user opts out of installments
        # entirely (see AGENTS.md's financial_profiles.csv description), even
        # if "installments" appears in payment_methods_user_will_consider.
        return []
    c = CANDIDATES["request_payment_options"]
    req_col = resolve_column(payment_options_df, c["request_id"])
    rows = payment_options_df[payment_options_df[req_col] == request_id]
    if rows.empty:
        return []

    method_col = resolve_column(payment_options_df, c["payment_method"], required=False)
    if method_col:
        rows = rows[rows[method_col].astype(str).str.strip().str.lower() == "installments"]
        if rows.empty:
            return []

    id_col = resolve_column(payment_options_df, c["payment_option_id"])
    start_col = resolve_column(payment_options_df, c["start_date"])
    n_col = resolve_column(payment_options_df, c["num_payments"])
    interval_col = resolve_column(payment_options_df, c["interval_days"], required=False)
    total_col = resolve_column(payment_options_df, c["total_payable_amount"])
    per_col = resolve_column(payment_options_df, c["per_payment_amount"], required=False)

    out: List[PaymentPlan] = []
    for _, row in rows.iterrows():
        start_date = row[start_col]
        n = int(row[n_col])
        interval = int(row[interval_col]) if interval_col and not pd.isna(row[interval_col]) else 0
        total = float(row[total_col])

        # ASSUMPTION (verify against real data): num_payments on a monthly-ish
        # cadence is treated as "months" directly for the max_installment_months
        # cap; as a fallback we also cap by actual elapsed duration in case
        # interval_days isn't ~30 (e.g. weekly installments over many months).
        duration_months = max(n, 1 + ((n - 1) * interval) // 30) if n > 0 else n
        if duration_months > ledger.max_installment_months:
            continue
        per_payment = float(row[per_col]) if per_col and not pd.isna(row[per_col]) else round(total / n, 2)

        payments = []
        running_total = 0.0
        for k in range(n):
            pay_date = start_date + dt.timedelta(days=interval * k)
            amt = per_payment
            if k == n - 1:
                # Last payment absorbs any rounding remainder so the schedule
                # sums exactly to the supplied total_payable_amount.
                amt = round(total - running_total, 2)
            payments.append((pay_date, amt))
            running_total += amt

        deadline_met = payments[-1][0] <= desired_completion_date
        if not deadline_met:
            # Per the 90-Day Safety Check, finishing by desired_completion_date
            # is part of what makes a plan safe at all (unlike `wait`, an
            # installment schedule that finishes late is excluded outright,
            # not merely deprioritized).
            continue
        plan = _try_with_and_without_changes(
            ledger, request_date, payments, deadline_met, "installments", payment_option_id=str(row[id_col])
        )
        if plan is not None:
            out.append(plan)
    return out
