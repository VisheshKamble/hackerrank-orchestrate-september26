"""
An immediate payment method is only eligible when the user's profile lists
it in payment_methods_user_will_consider. `wait` is eligible specifically
when the user accepts `full_payment` (since a wait plan culminates in a
full payment later) — this is called out explicitly in problem_statement.md,
not something we're inferring.
"""
from __future__ import annotations

from typing import List

from plans.candidates import PaymentPlan


def filter_eligible(plans: List[PaymentPlan], payment_methods_user_will_consider: set) -> List[PaymentPlan]:
    eligible = []
    for p in plans:
        required_method = "full_payment" if p.method == "wait" else p.method
        if required_method in payment_methods_user_will_consider:
            eligible.append(p)
    return eligible
