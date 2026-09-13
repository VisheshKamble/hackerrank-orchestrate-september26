"""
Column-name resolution helper.

IMPORTANT — READ THIS FIRST:
This solution was written from problem_statement.md's *prose description*
of financial_profiles.csv, financial_events.csv, request_payment_options.csv,
messages.csv and images.csv, not from the literal files (only requests.csv
was available while writing this code). Prose descriptions don't always
give exact column names.

Rather than hardcoding a column name and silently breaking (or worse,
silently reading the wrong column) if the real file differs, every column
read from these files goes through `resolve_column`, which:

  1. Tries a list of plausible candidate names (most-likely first).
  2. Raises a clear, actionable error listing the columns that DO exist
     if none of the candidates match.

The very first thing to do after cloning the real dataset is run
`python3 -m ingest.schema_report` (see bottom of this file) and fix any
`CANDIDATES` list below that doesn't match. That is a five-minute task
and it is the single highest-leverage thing to check before trusting
any downstream number.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd


class SchemaMismatchError(Exception):
    pass


def resolve_column(
    df: pd.DataFrame,
    candidates: Iterable[str],
    *,
    required: bool = True,
    context: str = "",
) -> Optional[str]:
    """Return the first column in `candidates` that exists in `df`."""
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand in df.columns:
            return cand
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    if required:
        raise SchemaMismatchError(
            f"None of the expected columns {list(candidates)} were found"
            f"{f' in {context}' if context else ''}. "
            f"Actual columns available: {list(df.columns)}. "
            f"Update the CANDIDATES list in ingest/schema.py to match."
        )
    return None


# ---------------------------------------------------------------------------
# Expected columns per file, most-likely name first. Patch these lists (not
# the call sites) if the real dataset uses different headers.
# ---------------------------------------------------------------------------
CANDIDATES = {
    "requests": {
        "request_id": ["request_id"],
        "user_id": ["user_id"],
        "request_date": ["request_date"],
        "request_type": ["request_type"],
        "requested_amount": ["requested_amount"],
        "desired_completion_date": ["desired_completion_date"],
        "allows_partial_payment": ["allows_partial_payment"],
        "request_text": ["request_text"],
    },
    "financial_profiles": {
        "user_id": ["user_id"],
        "home_currency": ["home_currency", "currency"],
        "available_balance": [
            "available_balance",
            "current_available_balance",
            "current_balance",
            "balance",
        ],
        "minimum_balance_to_keep": [
            "minimum_balance_to_keep",
            "minimum_balance",
            "min_balance_to_keep",
        ],
        "financial_priorities": ["financial_priorities", "priorities"],
        "payment_methods_user_will_consider": [
            "payment_methods_user_will_consider",
            "payment_preferences",
            "accepted_payment_methods",
        ],
        "spending_preferences": [
            "spending_preferences",
            "flexible_spending_preference",
            "expense_categories_user_is_willing_to_reduce",
            "expense_categories_user_is_willing_to_stop",
            "expense_categories_to_protect",
        ],
        # Blank/absent for a user who won't consider installments at all,
        # regardless of what's in payment_methods_user_will_consider — see
        # AGENTS.md's description of financial_profiles.csv. This is required=False
        # everywhere it's read; a missing column or blank cell both mean "no cap
        # data available", handled explicitly in ledger/build_ledger.py.
        "max_installment_months": ["max_installment_months"],
    },
    "financial_events": {
        "event_id": ["event_id"],
        "user_id": ["user_id"],
        "event_date": ["event_date", "date"],
        # Real dataset has this; cash-flow timing must prefer it over
        # event_date when present (see ledger/build_ledger.py). Optional
        # because not every dataset variant will have it.
        "settlement_date": ["settlement_date", "settled_on", "value_date"],
        "event_type": ["event_type", "category_type", "type"],
        "category": ["category", "description"],
        "amount": ["amount"],
        "currency": ["currency"],
        "is_recurring": ["is_recurring", "recurring"],
        "recurrence_frequency_days": [
            "recurrence_frequency_days",
            "recurrence_days",
            "frequency_days",
        ],
        # Real dataset uses a single string-valued "flexibility" field
        # ("flexible" / "protected" / etc.) instead of a boolean column —
        # ingest/loaders.py::_coerce_flexibility handles both conventions.
        "is_flexible": ["is_flexible", "flexible", "flexibility"],
        "status": ["status"],  # e.g. confirmed / pending / cancelled / settled / estimate
        "linked_event_id": ["linked_event_id"],
        "direction": ["direction", "flow"],  # income vs expense, if not inferable from sign
    },
    "exchange_rates": {
        "rate_date": ["rate_date", "date"],
        "from_currency": ["from_currency", "base_currency"],
        "to_currency": ["to_currency", "quote_currency"],
        "rate": ["rate", "exchange_rate"],
    },
    "request_payment_options": {
        "payment_option_id": ["payment_option_id"],
        "request_id": ["request_id"],
        "payment_method": ["payment_method", "method"],
        "start_date": ["start_date", "first_payment_date"],
        "num_payments": ["num_payments", "number_of_payments"],
        "interval_days": [
            "interval_days",
            "days_between_payments",
            "payment_frequency_days",
        ],
        "fee": ["fee", "financing_fee"],
        "total_payable_amount": ["total_payable_amount", "total_amount"],
        "per_payment_amount": [
            "per_payment_amount",
            "installment_amount",
            "payment_amount",
        ],
    },
    "messages": {
        "message_id": ["message_id"],
        "user_id": ["user_id"],
        "request_id": ["request_id"],
        "related_event_id": ["related_event_id"],
        "message_date": ["message_date", "date", "sent_at"],
        "message_text": ["message_text", "text", "content"],
    },
    "images": {
        "image_id": ["image_id"],
        "user_id": ["user_id"],
        "request_id": ["request_id"],
        "related_event_id": ["related_event_id"],
        "image_date": ["image_date", "date"],
    },
}


def schema_report(dataframes: dict) -> str:
    """Human-readable report of which candidate columns resolved to what."""
    lines = []
    for file_key, col_map in CANDIDATES.items():
        df = dataframes.get(file_key)
        lines.append(f"## {file_key}")
        if df is None:
            lines.append("  (file not loaded)")
            continue
        for logical_name, candidates in col_map.items():
            resolved = resolve_column(df, candidates, required=False)
            status = resolved if resolved else "!! NOT FOUND !!"
            lines.append(f"  {logical_name:36s} -> {status}")
        lines.append("")
    return "\n".join(lines)
