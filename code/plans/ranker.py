"""
Implements, in exact order, the tie-break rules from problem_statement.md
("Choosing Between Safe Plans"):

  1. Complete the full request by desired_completion_date.
  2. Require no spending changes.
  3. Minimize the total amount paid.
  4. Start payment earlier.
  5. Use fewer payments.
  6. Use the lowest payment_option_id as the final tie-breaker.

Implemented as a single sort key tuple so the ordering is unambiguous and
trivially unit-testable — see tests/test_ranker.py.
"""
from __future__ import annotations

import re
from typing import List, Optional

from plans.candidates import PaymentPlan


def _option_id_key(payment_option_id: Optional[str]):
    if payment_option_id is None:
        return (1, 0)  # sorts after any real payment_option_id
    digits = re.findall(r"\d+", payment_option_id)
    if digits:
        return (0, int(digits[0]))
    return (0, payment_option_id)  # non-numeric id: lexical fallback


def sort_key(plan: PaymentPlan):
    return (
        0 if plan.deadline_met else 1,          # rule 1: deadline met sorts first
        0 if not plan.spending_changes else 1,  # rule 2: no changes sorts first
        plan.total_paid,                         # rule 3: lower total paid sorts first
        plan.start_date,                         # rule 4: earlier start sorts first
        plan.num_payments,                       # rule 5: fewer payments sorts first
        _option_id_key(plan.payment_option_id),  # rule 6: lowest payment_option_id
    )


def rank(plans: List[PaymentPlan]) -> List[PaymentPlan]:
    return sorted(plans, key=sort_key)


def best_plan(plans: List[PaymentPlan]) -> Optional[PaymentPlan]:
    ranked = rank(plans)
    return ranked[0] if ranked else None
