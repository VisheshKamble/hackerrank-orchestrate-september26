"""
Unit tests for forecast/simulator.py + amount_safe_to_pay.py +
earliest_full_payment.py, using a small hand-worked synthetic ledger so the
expected numbers can be verified by hand (see comments) rather than trusting
the code to check itself.

Run with:
    python3 -m pytest code/tests/test_simulator.py -v
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forecast.amount_safe_to_pay import compute_amount_safe_to_pay
from forecast.earliest_full_payment import compute_earliest_date_for_full_payment
from forecast.simulator import build_balance_series, simulate_with_payments, suffix_min
from ledger.build_ledger import Ledger, OneTimeEvent, RecurringEvent

START = dt.date(2026, 1, 1)


def make_ledger() -> Ledger:
    return Ledger(
        user_id="u1",
        home_currency="INR",
        available_balance=1000.0,
        minimum_balance_to_keep=200.0,
        payment_methods_user_will_consider={"full_payment", "partial_payment", "installments"},
        recurring_events=[
            RecurringEvent(
                event_id="e_rent",
                signed_amount=-100.0,
                reference_date=START,  # occurs on day 0, day 30, day 60...
                frequency_days=30,
                flexible=False,
                category="rent",
            )
        ],
        one_time_events=[
            OneTimeEvent(event_id="e_bonus", date=START + dt.timedelta(days=45), signed_amount=500.0, category="bonus")
        ],
    )


def test_balance_series_matches_hand_calculation():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    # Hand-worked: 1000 -100(day0) flat to day29 -> 900
    assert series[0] == 900.0
    assert series[29] == 900.0
    # -100 again on day30 -> 800, flat to day44
    assert series[30] == 800.0
    assert series[44] == 800.0
    # +500 on day45 -> 1300, flat to day59
    assert series[45] == 1300.0
    assert series[59] == 1300.0
    # -100 on day60 -> 1200, flat to day89
    assert series[60] == 1200.0
    assert series[89] == 1200.0


def test_suffix_min():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    sm = suffix_min(series)
    assert sm[0] == 800.0  # global minimum across the whole window
    assert sm[45] == 1200.0  # minimum from day45 onward


def test_amount_safe_to_pay_full_amount_is_safe():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    # room = suffix_min[0] - min_balance = 800 - 200 = 600
    assert compute_amount_safe_to_pay(series, ledger.minimum_balance_to_keep, 600.0) == 600.0


def test_amount_safe_to_pay_caps_at_available_room():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    # requesting more than the 600 of headroom should cap at 600, not 850
    assert compute_amount_safe_to_pay(series, ledger.minimum_balance_to_keep, 850.0) == 600.0


def test_earliest_date_for_full_payment():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    # 850 needs suffix_min[d] >= 850 + 200 = 1050, which first holds at day45
    earliest = compute_earliest_date_for_full_payment(series, START, ledger.minimum_balance_to_keep, 850.0)
    assert earliest == START + dt.timedelta(days=45)


def test_earliest_date_equals_request_date_when_already_safe():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    earliest = compute_earliest_date_for_full_payment(series, START, ledger.minimum_balance_to_keep, 600.0)
    assert earliest == START


def test_earliest_date_none_when_never_safe():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    earliest = compute_earliest_date_for_full_payment(series, START, ledger.minimum_balance_to_keep, 10_000.0)
    assert earliest is None


def test_simulate_with_payments_multi_installment():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    # Two payments of 400 each on day0 and day45 -> cumulative impact 400 then 800.
    # series[0]=900-400=500 (safe, >=200); series[44]=800-400=400 (safe);
    # series[45]=1300-800=500 (safe) ... all remain >=200 => should be safe.
    safe, first_violation = simulate_with_payments(series, [(0, 400.0), (45, 400.0)], ledger.minimum_balance_to_keep)
    assert safe is True
    assert first_violation is None


def test_simulate_with_payments_detects_violation():
    ledger = make_ledger()
    series = build_balance_series(ledger, START, days=90)
    # A single 750 payment on day0 drops day0 balance to 900-750=150 < 200 immediately.
    safe, first_violation = simulate_with_payments(series, [(0, 750.0)], ledger.minimum_balance_to_keep)
    assert safe is False
    assert first_violation == 0
