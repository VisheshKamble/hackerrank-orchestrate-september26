"""
Orchestrates one request_id end to end:
  resolve unstructured evidence -> build ledger -> forecast -> generate &
  rank candidate plans -> format the output.csv row.

Kept as a single readable function (not a class) because every step is a
straight pipe with no shared mutable state beyond what's passed explicitly —
that makes it easy to unit test each stage independently (see tests/) and
easy to explain step by step in the AI Judge interview.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from config import settings
from forecast.amount_safe_to_pay import compute_amount_safe_to_pay
from forecast.earliest_full_payment import compute_earliest_date_for_full_payment
from forecast.simulator import build_balance_series
from ingest.currency import RateTable
from ingest.schema import CANDIDATES, resolve_column
from ledger.build_ledger import build_ledger
from plans.candidates import (
    full_payment_candidate,
    installment_candidates,
    partial_payment_candidate,
    wait_candidate,
)
from plans.eligibility import filter_eligible
from plans.ranker import best_plan
from resolve.cache import DiskCache
from resolve.image_extractor import extract_amount_from_image
from resolve.message_parser import build_message_actions
from explain.narrator import generate_explanation
from usage_tracker import UsageTracker

log = logging.getLogger("pipeline")


def _format_payment_plan(payments) -> str:
    if not payments:
        return "none"
    def format_amount(amount) -> str:
        numeric = float(amount)
        return str(int(numeric)) if numeric.is_integer() else str(numeric)

    return "|".join(f"{d.isoformat()}:{format_amount(amt)}" for d, amt in payments)


def _format_spending_changes(changes) -> str:
    if not changes:
        return "none"
    parts = []
    for action, event_id, value in changes[: settings.MAX_SPENDING_CHANGES]:
        if action == "stop":
            parts.append(f"stop:{event_id}")
        else:
            parts.append(f"reduce_to:{event_id}:{value}")
    return "|".join(parts)


def _blank_amount_event_ids_for_user(events_df: pd.DataFrame, user_id: str) -> list:
    c = CANDIDATES["financial_events"]
    user_col = resolve_column(events_df, c["user_id"])
    amount_col = resolve_column(events_df, c["amount"])
    event_col = resolve_column(events_df, c["event_id"])
    sub = events_df[(events_df[user_col] == user_id) & (events_df[amount_col].isna())]
    return sub[event_col].tolist()


def _images_for_events(images_df: pd.DataFrame, event_ids: list) -> Dict[str, Path]:
    if images_df is None or images_df.empty or not event_ids:
        return {}
    c = CANDIDATES["images"]
    related_col = resolve_column(images_df, c["related_event_id"], required=False)
    id_col = resolve_column(images_df, c["image_id"], required=False)
    if not related_col or not id_col:
        return {}
    out = {}
    for _, row in images_df.iterrows():
        eid = row.get(related_col)
        if eid in event_ids:
            out[eid] = settings.MEDIA_IMAGES_DIR / f"{row[id_col]}.png"
    return out


def _messages_for_events(messages_df: pd.DataFrame, event_ids: list) -> list:
    if messages_df is None or messages_df.empty or not event_ids:
        return []
    c = CANDIDATES["messages"]
    related_col = resolve_column(messages_df, c["related_event_id"], required=False)
    text_col = resolve_column(messages_df, c["message_text"], required=False)
    date_col = resolve_column(messages_df, c["message_date"], required=False)
    id_col = resolve_column(messages_df, c["message_id"], required=False)
    if not related_col or not text_col:
        return []
    out = []
    for _, row in messages_df.iterrows():
        eid = row.get(related_col)
        if eid in event_ids:
            out.append(
                {
                    "event_id": eid,
                    "message_id": row.get(id_col) if id_col else None,
                    "message_text": str(row[text_col]),
                    "message_date": row.get(date_col) if date_col else None,
                }
            )
    return out


def process_request(
    request_row: pd.Series,
    data: Dict[str, pd.DataFrame],
    rate_table: RateTable,
    tracker: Optional[UsageTracker] = None,
    cache: Optional[DiskCache] = None,
) -> dict:
    rc = CANDIDATES["requests"]
    request_id = str(request_row[resolve_column(request_row.to_frame().T, rc["request_id"])])
    user_id = str(request_row[resolve_column(request_row.to_frame().T, rc["user_id"])])
    request_date: dt.date = request_row[resolve_column(request_row.to_frame().T, rc["request_date"])]
    desired_completion_date: dt.date = request_row[
        resolve_column(request_row.to_frame().T, rc["desired_completion_date"])
    ]
    requested_amount = float(request_row[resolve_column(request_row.to_frame().T, rc["requested_amount"])])
    allows_partial_payment = bool(request_row[resolve_column(request_row.to_frame().T, rc["allows_partial_payment"])])

    # --- profile lookup ---------------------------------------------------
    profiles_df = data["financial_profiles"]
    pc = CANDIDATES["financial_profiles"]
    user_col = resolve_column(profiles_df, pc["user_id"])
    profile_rows = profiles_df[profiles_df[user_col] == user_id]
    if profile_rows.empty:
        raise ValueError(f"No financial_profiles.csv row for user_id={user_id}")
    profile_row = profile_rows.iloc[0]

    # --- resolve unstructured evidence: blank-amount images + messages ----
    events_df = data["financial_events"]
    blank_event_ids = _blank_amount_event_ids_for_user(events_df, user_id)
    image_paths = _images_for_events(data.get("images", pd.DataFrame()), blank_event_ids)
    image_amounts = {eid: extract_amount_from_image(path, tracker, cache) for eid, path in image_paths.items()}
    image_amounts = {k: v for k, v in image_amounts.items() if v is not None}

    # All event_ids for this user get a chance to pick up amendment messages,
    # not just the blank-amount ones — a message can amend/cancel a
    # perfectly well-formed row too.
    c = CANDIDATES["financial_events"]
    all_event_ids = events_df[events_df[resolve_column(events_df, c["user_id"])] == user_id][
        resolve_column(events_df, c["event_id"])
    ].tolist()
    relevant_messages = _messages_for_events(data.get("messages", pd.DataFrame()), all_event_ids)
    message_actions = build_message_actions(relevant_messages, tracker, cache)

    # --- build ledger + forecast -------------------------------------------
    ledger = build_ledger(
        user_id=user_id,
        profile_row=profile_row,
        events_df_all=events_df,
        rate_table=rate_table,
        image_amounts=image_amounts,
        message_actions=message_actions,
        as_of_date=request_date,
    )

    baseline_series = build_balance_series(ledger, request_date)
    amount_safe_to_pay = compute_amount_safe_to_pay(baseline_series, ledger.minimum_balance_to_keep, requested_amount)
    earliest_date = compute_earliest_date_for_full_payment(
        baseline_series, request_date, ledger.minimum_balance_to_keep, requested_amount
    )

    # --- generate + rank candidate plans ------------------------------------
    candidates = []
    fp = full_payment_candidate(ledger, request_date, requested_amount, desired_completion_date)
    if fp:
        candidates.append(fp)
    wc = wait_candidate(ledger, earliest_date, requested_amount, desired_completion_date, request_date)
    if wc:
        candidates.append(wc)
    pp = partial_payment_candidate(
        ledger, request_date, requested_amount, amount_safe_to_pay, earliest_date, desired_completion_date, allows_partial_payment
    )
    if pp:
        candidates.append(pp)
    candidates.extend(
        installment_candidates(ledger, request_date, request_id, data.get("request_payment_options", pd.DataFrame()), desired_completion_date)
    )

    eligible = filter_eligible(candidates, ledger.payment_methods_user_will_consider)
    chosen = best_plan(eligible)

    # --- translate the chosen plan into output fields -----------------------
    if chosen is None:
        affordability_status = "not_affordable"
        recommended_payment_method = "not_recommended"
        payment_plan_str = "none"
        spending_changes_str = "none"
        earliest_date_out = earliest_date  # still report capacity even with no eligible method
    else:
        recommended_payment_method = chosen.method
        payment_plan_str = _format_payment_plan(chosen.payments)
        spending_changes_str = _format_spending_changes(chosen.spending_changes)
        if chosen.method == "wait":
            affordability_status = "affordable_later"
        elif chosen.method == "full_payment":
            affordability_status = "affordable_now" if not chosen.spending_changes else "affordable_with_plan"
        else:  # partial_payment, installments
            affordability_status = "affordable_with_plan"
        earliest_date_out = earliest_date

    earliest_date_field = earliest_date_out.isoformat() if earliest_date_out else ""

    # NOTE: earliest_date_for_full_payment is a standalone metric computed
    # WITHOUT spending changes (per spec) — it can be empty even when the
    # chosen plan (which may use spending changes) completes today. The
    # explanation should describe what the chosen plan actually does, so it
    # uses the plan's own last payment date, not this metric.
    plan_completion_date = chosen.payments[-1][0].isoformat() if chosen else None

    facts = {
        "affordability_status": affordability_status,
        "recommended_payment_method": recommended_payment_method,
        "amount_safe_to_pay": amount_safe_to_pay,
        "requested_amount": requested_amount,
        "currency": ledger.home_currency,
        "minimum_balance": ledger.minimum_balance_to_keep,
        "request_date": request_date.isoformat(),
        "deadline": desired_completion_date.isoformat(),
        "earliest_date": plan_completion_date or earliest_date_field or "beyond the 90-day forecast",
        "method": recommended_payment_method,
    }
    decision_explanation = generate_explanation(facts, tracker, cache)

    return {
        "request_id": request_id,
        "amount_safe_to_pay": amount_safe_to_pay,
        "affordability_status": affordability_status,
        "recommended_payment_method": recommended_payment_method,
        "payment_plan": payment_plan_str,
        "earliest_date_for_full_payment": earliest_date_field,
        "spending_changes_needed": spending_changes_str,
        "decision_explanation": decision_explanation,
    }
