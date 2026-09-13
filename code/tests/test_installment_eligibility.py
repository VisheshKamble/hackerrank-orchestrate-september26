"""
Unit tests for the max_installment_months cap added to
plans/candidates.py::installment_candidates (see ledger/build_ledger.py and
ingest/schema.py for where the value comes from).

Per AGENTS.md's description of financial_profiles.csv: "max_installment_months
is blank when the user will not consider installments" — i.e. a blank value
means NO installment plan is ever eligible, even if "installments" appears
in payment_methods_user_will_consider. A non-blank value caps how long a
plan may run.

Run with:
    python3 -m pytest code/tests/test_installment_eligibility.py -v
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledger.build_ledger import Ledger
from plans.candidates import installment_candidates

START = dt.date(2026, 1, 1)
DEADLINE = dt.date(2026, 12, 31)


def _ledger(max_installment_months):
    return Ledger(
        user_id="u1",
        home_currency="USD",
        available_balance=100_000.0,  # plenty of headroom — isolates the eligibility check
        minimum_balance_to_keep=0.0,
        payment_methods_user_will_consider={"installments"},
        max_installment_months=max_installment_months,
        recurring_events=[],
        one_time_events=[],
    )


def _options_df():
    return pd.DataFrame(
        [
            {
                "payment_option_id": "opt_1",
                "request_id": "req_1",
                "start_date": START,
                "num_payments": 3,
                "interval_days": 30,
                "total_payable_amount": 300.0,
            },
            {
                "payment_option_id": "opt_2",
                "request_id": "req_1",
                "start_date": START,
                "num_payments": 12,
                "interval_days": 30,
                "total_payable_amount": 1200.0,
            },
        ]
    )


def test_blank_max_installment_months_disables_installments_entirely():
    ledger = _ledger(None)
    plans = installment_candidates(ledger, START, "req_1", _options_df(), DEADLINE)
    assert plans == []


def test_option_within_cap_is_offered():
    ledger = _ledger(6)
    plans = installment_candidates(ledger, START, "req_1", _options_df(), DEADLINE)
    ids = {p.payment_option_id for p in plans}
    assert "opt_1" in ids  # 3 monthly payments <= 6-month cap


def test_option_exceeding_cap_is_excluded():
    ledger = _ledger(6)
    plans = installment_candidates(ledger, START, "req_1", _options_df(), DEADLINE)
    ids = {p.payment_option_id for p in plans}
    assert "opt_2" not in ids  # 12 monthly payments > 6-month cap
