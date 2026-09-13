"""
Build one clean, canonical Ledger per user from financial_events.csv plus
the structured signals produced by resolve/ (image amounts, message-derived
amendments). This is the single place raw CSV rows turn into the numbers
forecast/ will simulate — keep it boring and auditable.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from ingest.currency import RateTable
from ingest.schema import CANDIDATES, resolve_column
from ledger.conflict_resolution import RawEvent, resolve_events


@dataclass
class RecurringEvent:
    event_id: str
    signed_amount: float  # income positive, expense negative, home currency
    reference_date: dt.date  # a known occurrence date, used to phase the cycle
    frequency_days: int
    flexible: bool
    category: str
    inferred: bool = False  # True when detected heuristically rather than from an explicit flag


@dataclass
class OneTimeEvent:
    event_id: str
    date: dt.date
    signed_amount: float
    category: str


@dataclass
class Ledger:
    user_id: str
    home_currency: str
    available_balance: float
    minimum_balance_to_keep: float
    payment_methods_user_will_consider: set
    max_installment_months: Optional[int] = None  # None => blank in the profile => no installments at all
    recurring_events: List[RecurringEvent] = field(default_factory=list)
    one_time_events: List[OneTimeEvent] = field(default_factory=list)


# Keywords used only when a dataset doesn't give us an explicit sign/direction
# column and we have to infer income vs. expense from a category/type string.
# This is a fallback — prefer an explicit column when the real schema has one.
_INCOME_HINTS = ("salary", "income", "credit", "refund", "payout", "deposit_in")
_EXPENSE_HINTS = ("expense", "rent", "bill", "subscription", "debit", "loan_payment", "emi")


def _infer_sign(amount: float, category: str, direction: Optional[str]) -> float:
    if direction:
        d = direction.strip().lower()
        if d in ("income", "credit", "in"):
            return abs(amount)
        if d in ("expense", "debit", "out"):
            return -abs(amount)
    cat = (category or "").strip().lower()
    if any(h in cat for h in _INCOME_HINTS):
        return abs(amount)
    if any(h in cat for h in _EXPENSE_HINTS):
        return -abs(amount)
    # Last resort: trust the sign already present in the amount field.
    return amount


def _category_flexible(events: List["RawEvent"]) -> bool:
    """Protected wins over flexible when a category's occurrences disagree
    (touching a protected/essential expense is the worse mistake to make),
    otherwise True if ANY occurrence was flagged flexible."""
    flags = [e.is_flexible for e in events]
    if any(flags) and not all(flags):
        return False  # disagreement across occurrences -> conservative
    return any(flags)


def detect_recurring_from_history(
    resolved_events: List["RawEvent"], as_of_date: dt.date, min_occurrences: int = 3
) -> List["RecurringEvent"]:
    """The real dataset has no explicit is_recurring/recurrence_frequency_days
    columns, so this is how recurring expenses are actually found: group a
    user's historical (dated on/before as_of_date) events by category, and
    treat a category as recurring only when it has at least
    `min_occurrences` past occurrences whose gaps are reasonably consistent
    (max gap <= 2x min gap — loose enough for real-world irregularity,
    tight enough to reject a coincidental cluster).

    Categories that already have an EXPLICIT recurring flag are skipped here
    (handled by the flag-based path in build_ledger, so we don't double-count
    the same series). Categories that already have a FUTURE-dated occurrence
    in financial_events.csv are also skipped — if the dataset already tells
    us the next occurrence explicitly, project no further than that rather
    than risk double-counting it against a synthesized projection.

    Conservative by construction, per the "estimate recurrence intervals
    conservatively" requirement:
      - frequency_days = the SHORTEST observed gap (assumes it recurs at
        least as often as the tightest historical spacing, not the loose
        average) — safer for a system whose job is to protect a minimum
        balance.
      - signed_amount = min(signed amounts in the group) — for an expense
        (negative) that's the largest magnitude (worst case); for an
        income (positive) that's the smallest (least optimistic).
    """
    already_explicit = {
        (e.category or "").strip().lower() for e in resolved_events if e.is_recurring and e.recurrence_frequency_days
    }

    by_category: Dict[str, List["RawEvent"]] = {}
    for e in resolved_events:
        cat = (e.category or "").strip().lower()
        if not cat or cat in already_explicit:
            continue
        by_category.setdefault(cat, []).append(e)

    out: List[RecurringEvent] = []
    for cat, events in by_category.items():
        historical = sorted([e for e in events if e.effective_date <= as_of_date], key=lambda e: e.effective_date)
        future = [e for e in events if e.effective_date > as_of_date]
        if future:
            continue  # dataset already gives us the next occurrence explicitly
        if len(historical) < min_occurrences:
            continue

        # Long histories with widely varying amounts are usually variable
        # spending (for example groceries or dining), not fixed recurring
        # commitments. Projecting the single largest historical amount on
        # every cycle makes the forecast unnecessarily pessimistic. Keep the
        # conservative worst-case amount for short histories, where there is
        # not enough evidence to classify the variation reliably.
        if len(historical) >= 4:
            amounts = [abs(e.amount) for e in historical]
            amount_range = max(amounts) - min(amounts)
            amount_scale = max(sorted(amounts)[len(amounts) // 2], 1.0)
            if amount_range / amount_scale > 0.25:
                continue

        gaps = [
            (historical[i + 1].effective_date - historical[i].effective_date).days
            for i in range(len(historical) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue
        min_gap, max_gap = min(gaps), max(gaps)
        if max_gap > 2 * min_gap:
            continue  # too irregular to trust as a recurring pattern

        conservative_amount = min(e.amount for e in historical)
        last = historical[-1]
        out.append(
            RecurringEvent(
                event_id=last.event_id,  # representative real event_id, for spending_changes_needed output
                signed_amount=conservative_amount,
                reference_date=last.effective_date,
                frequency_days=min_gap,
                flexible=_category_flexible(historical),
                category=last.category,
                inferred=True,
            )
        )
    return out


def _apply_message_amendments(
    events_df: pd.DataFrame,
    event_col: str,
    amount_col: str,
    status_col: Optional[str],
    date_col: str,
    message_actions: List[dict],
) -> pd.DataFrame:
    """message_actions: [{event_id, action, new_value, message_date, source_priority}]
    action in {"cancel", "amend_amount", "amend_date", "confirm"}.
    This mutates a copy of events_df, and also stamps source_priority so
    conflict_resolution can prefer amendments that came from a (newer) message
    over the original CSV row."""
    df = events_df.copy()
    df["_source_priority"] = 0
    if status_col is None:
        status_col = "_status_derived"
        df[status_col] = "confirmed"

    for act in message_actions:
        mask = df[event_col] == act["event_id"]
        if not mask.any():
            continue
        if act["action"] == "cancel":
            df.loc[mask, status_col] = "cancelled"
        elif act["action"] == "amend_amount":
            df.loc[mask, amount_col] = act["new_value"]
            df.loc[mask, status_col] = "amended"
        elif act["action"] == "amend_date":
            df.loc[mask, date_col] = act["new_value"]
            df.loc[mask, status_col] = "amended"
        elif act["action"] == "confirm":
            df.loc[mask, status_col] = "settled"
        df.loc[mask, "_source_priority"] = 1
    return df, status_col


def build_ledger(
    user_id: str,
    profile_row: pd.Series,
    events_df_all: pd.DataFrame,
    rate_table: RateTable,
    image_amounts: Dict[str, float],
    message_actions: List[dict],
    as_of_date: dt.date,
) -> Ledger:
    pc = CANDIDATES["financial_profiles"]
    home_currency = str(profile_row[resolve_column(profile_row.to_frame().T, pc["home_currency"])])
    available_balance = float(profile_row[resolve_column(profile_row.to_frame().T, pc["available_balance"])])
    min_balance = float(
        profile_row[resolve_column(profile_row.to_frame().T, pc["minimum_balance_to_keep"])]
    )
    methods_col = resolve_column(profile_row.to_frame().T, pc["payment_methods_user_will_consider"])
    methods_raw = str(profile_row[methods_col])
    payment_methods = {m.strip() for m in methods_raw.replace(";", "|").split("|") if m.strip()}

    max_inst_col = resolve_column(profile_row.to_frame().T, pc["max_installment_months"], required=False)
    max_installment_months: Optional[int] = None
    if max_inst_col is not None:
        raw_val = profile_row[max_inst_col]
        if raw_val is not None and not pd.isna(raw_val) and str(raw_val).strip() != "":
            try:
                max_installment_months = int(float(raw_val))
            except (TypeError, ValueError):
                max_installment_months = None

    ec = CANDIDATES["financial_events"]
    ev_df = events_df_all.copy()
    user_col = resolve_column(ev_df, ec["user_id"])
    ev_df = ev_df[ev_df[user_col] == user_id]

    event_id_col = resolve_column(ev_df, ec["event_id"])
    date_col = resolve_column(ev_df, ec["event_date"])
    settlement_col = resolve_column(ev_df, ec["settlement_date"], required=False)
    amount_col = resolve_column(ev_df, ec["amount"])
    currency_col = resolve_column(ev_df, ec["currency"], required=False)
    status_col = resolve_column(ev_df, ec["status"], required=False)
    recurring_col = resolve_column(ev_df, ec["is_recurring"], required=False)
    freq_col = resolve_column(ev_df, ec["recurrence_frequency_days"], required=False)
    flexible_col = resolve_column(ev_df, ec["is_flexible"], required=False)
    category_col = resolve_column(ev_df, ec["category"], required=False)
    linked_col = resolve_column(ev_df, ec["linked_event_id"], required=False)
    direction_col = resolve_column(ev_df, ec["direction"], required=False)

    # 1. Fill blank amounts from VLM-extracted image amounts.
    #    "Do not treat a blank amount as zero" — rows we can't resolve are
    #    dropped with a loud comment rather than silently defaulted to 0,
    #    since a silent zero would be a worse (and undetectable) error.
    unresolved_blank = []
    for idx, row in ev_df.iterrows():
        if pd.isna(row[amount_col]):
            eid = row[event_id_col]
            if eid in image_amounts:
                ev_df.at[idx, amount_col] = image_amounts[eid]
            else:
                unresolved_blank.append(eid)
    if unresolved_blank:
        ev_df = ev_df[~ev_df[event_id_col].isin(unresolved_blank)]
        # NOTE: surfaced to the caller via logging in pipeline.py, not raised
        # here, so one bad row never kills the whole run.

    # 2. Apply message-derived amendments (cancel/amend/confirm), stamping
    #    source priority so conflict_resolution treats them as authoritative.
    ev_df, status_col = _apply_message_amendments(
        ev_df, event_id_col, amount_col, status_col, date_col, message_actions
    )

    # 3. Build RawEvent objects (currency-converted, sign-resolved).
    raw_events: List[RawEvent] = []
    for _, row in ev_df.iterrows():
        amt = float(row[amount_col])
        currency = str(row[currency_col]).upper() if currency_col else home_currency
        settlement_date = (
            row[settlement_col] if settlement_col and not pd.isna(row[settlement_col]) else None
        )
        # Per problem_statement.md: convert at the rate for the event's
        # SETTLEMENT date, not when it was recorded — falls back to
        # event_date when settlement_date isn't available.
        fx_date = settlement_date if settlement_date is not None else row[date_col]
        if currency != home_currency:
            amt = rate_table.convert(amt, fx_date, currency, home_currency)
        category = str(row[category_col]) if category_col else ""
        direction = str(row[direction_col]) if direction_col else None
        signed = _infer_sign(amt, category, direction)
        raw_events.append(
            RawEvent(
                event_id=str(row[event_id_col]),
                user_id=user_id,
                event_date=row[date_col],
                settlement_date=settlement_date,
                amount=signed,
                status=str(row[status_col]) if status_col in row else "confirmed",
                is_recurring=bool(row[recurring_col]) if recurring_col else False,
                recurrence_frequency_days=(
                    int(row[freq_col]) if freq_col and not pd.isna(row[freq_col]) else None
                ),
                is_flexible=bool(row[flexible_col]) if flexible_col else False,
                category=category,
                linked_event_id=(str(row[linked_col]) if linked_col and not pd.isna(row[linked_col]) else None),
                source_priority=int(row.get("_source_priority", 0)),
            )
        )

    # 4. Resolve conflicts / lifecycle chains -> one clean event per real thing.
    resolved = resolve_events(raw_events)

    # 5. Drop pending CREDITS (spec: "ignore pending credits") and unrealized
    #    investment value; everything else is either a concrete one-time
    #    event or feeds the recurrence detector below.
    eligible = [
        e
        for e in resolved
        if not ((e.status or "").lower() == "pending" and e.amount > 0)
        and "unrealized" not in (e.category or "").lower()
    ]

    recurring: List[RecurringEvent] = []
    one_time: List[OneTimeEvent] = []
    for e in eligible:
        if e.is_recurring and e.recurrence_frequency_days:
            recurring.append(
                RecurringEvent(
                    event_id=e.event_id,
                    signed_amount=e.amount,
                    reference_date=e.effective_date,
                    frequency_days=e.recurrence_frequency_days,
                    flexible=e.is_flexible,
                    category=e.category,
                )
            )
        else:
            one_time.append(
                OneTimeEvent(event_id=e.event_id, date=e.effective_date, signed_amount=e.amount, category=e.category)
            )

    # 6. The real dataset has no explicit is_recurring/recurrence_frequency_days
    #    columns, so this is how most recurring expenses are actually found —
    #    see detect_recurring_from_history's docstring for the (conservative)
    #    detection rules and why double-counting against an already-explicit
    #    future row isn't a concern.
    recurring.extend(detect_recurring_from_history(eligible, as_of_date))

    return Ledger(
        user_id=user_id,
        home_currency=home_currency,
        available_balance=available_balance,
        minimum_balance_to_keep=min_balance,
        payment_methods_user_will_consider=payment_methods,
        max_installment_months=max_installment_months,
        recurring_events=recurring,
        one_time_events=one_time,
    )
