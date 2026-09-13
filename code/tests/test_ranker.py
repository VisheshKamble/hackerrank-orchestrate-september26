"""
Unit tests for plans/ranker.py — verifies the exact 6-rule tie-break order
from problem_statement.md using small, deliberately-crafted candidate sets.

Run with:
    python3 -m pytest code/tests/test_ranker.py -v
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plans.candidates import PaymentPlan
from plans.ranker import best_plan, rank

D0 = dt.date(2026, 1, 1)
D10 = dt.date(2026, 1, 11)
D20 = dt.date(2026, 1, 21)


def test_rule1_deadline_met_beats_missed_deadline():
    missed = PaymentPlan("wait", [(D20, 500)], deadline_met=False, total_paid=500, spending_changes=[])
    met = PaymentPlan("installments", [(D10, 500)], deadline_met=True, total_paid=500, spending_changes=[])
    assert best_plan([missed, met]) is met


def test_rule2_no_spending_changes_beats_needing_changes():
    needs_changes = PaymentPlan("full_payment", [(D0, 500)], deadline_met=True, total_paid=500, spending_changes=[("stop", "e1", None)])
    clean = PaymentPlan("partial_payment", [(D0, 200), (D10, 300)], deadline_met=True, total_paid=500, spending_changes=[])
    assert best_plan([needs_changes, clean]) is clean


def test_rule3_minimize_total_paid():
    expensive = PaymentPlan("installments", [(D0, 550)], deadline_met=True, total_paid=550, spending_changes=[], payment_option_id="2")
    cheap = PaymentPlan("full_payment", [(D0, 500)], deadline_met=True, total_paid=500, spending_changes=[])
    assert best_plan([expensive, cheap]) is cheap


def test_rule4_earlier_start_wins_when_total_paid_ties():
    later = PaymentPlan("wait", [(D10, 500)], deadline_met=True, total_paid=500, spending_changes=[])
    earlier = PaymentPlan("full_payment", [(D0, 500)], deadline_met=True, total_paid=500, spending_changes=[])
    assert best_plan([later, earlier]) is earlier


def test_rule5_fewer_payments_wins():
    two_payments = PaymentPlan("installments", [(D0, 250), (D10, 250)], deadline_met=True, total_paid=500, spending_changes=[], payment_option_id="5")
    one_payment = PaymentPlan("full_payment", [(D0, 500)], deadline_met=True, total_paid=500, spending_changes=[])
    assert best_plan([two_payments, one_payment]) is one_payment


def test_rule6_lowest_payment_option_id_is_final_tiebreak():
    opt3 = PaymentPlan("installments", [(D0, 250), (D10, 250)], deadline_met=True, total_paid=500, spending_changes=[], payment_option_id="opt_3")
    opt1 = PaymentPlan("installments", [(D0, 250), (D10, 250)], deadline_met=True, total_paid=500, spending_changes=[], payment_option_id="opt_1")
    assert best_plan([opt3, opt1]) is opt1


def test_full_ranking_order_is_stable():
    plans = [
        PaymentPlan("wait", [(D20, 500)], deadline_met=False, total_paid=500, spending_changes=[]),
        PaymentPlan("full_payment", [(D0, 500)], deadline_met=True, total_paid=500, spending_changes=[("stop", "e1", None)]),
        PaymentPlan("partial_payment", [(D0, 200), (D10, 300)], deadline_met=True, total_paid=500, spending_changes=[]),
    ]
    ordered = rank(plans)
    assert ordered[0].method == "partial_payment"  # deadline met + no changes beats everything else here
    assert ordered[-1].method == "wait"  # missed deadline sorts last


def test_no_plans_returns_none():
    assert best_plan([]) is None
