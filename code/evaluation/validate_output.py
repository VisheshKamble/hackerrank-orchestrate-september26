#!/usr/bin/env python3
"""
Deterministic sanity checks on output.csv, run BEFORE submission.
Not a scorer against ground truth (we don't have it) — this only catches
structural mistakes that would cost points regardless of the hidden answers.

Run with:
    python3 code/evaluation/validate_output.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow `config`, `ingest` imports

from config import settings
from ingest.loaders import load_all
from ingest.schema import CANDIDATES, resolve_column

PAYMENT_PLAN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}:-?\d+(\.\d+)?$")
SPENDING_CHANGE_RE = re.compile(r"^(stop:[\w-]+|reduce_to:[\w-]+:-?\d+(\.\d+)?)$")


def validate(output_path: Path, requests_df: pd.DataFrame) -> list:
    errors = []
    if not output_path.exists():
        return [f"{output_path} does not exist"]
    out = pd.read_csv(output_path, dtype=str)

    if list(out.columns) != settings.OUTPUT_COLUMNS:
        errors.append(f"Column mismatch. Expected {settings.OUTPUT_COLUMNS}, got {list(out.columns)}")

    rc = CANDIDATES["requests"]
    rid_col = resolve_column(requests_df, rc["request_id"])
    amt_col = resolve_column(requests_df, rc["requested_amount"])
    date_col = resolve_column(requests_df, rc["request_date"])

    req_ids = set(requests_df[rid_col].astype(str))
    out_ids = list(out["request_id"].astype(str))
    if len(out_ids) != len(requests_df):
        errors.append(f"Row count mismatch: output has {len(out_ids)}, requests.csv has {len(requests_df)}")
    missing = req_ids - set(out_ids)
    extra = set(out_ids) - req_ids
    dupes = [r for r in set(out_ids) if out_ids.count(r) > 1]
    if missing:
        errors.append(f"Missing request_ids in output: {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}")
    if extra:
        errors.append(f"Unexpected request_ids in output: {sorted(extra)[:10]}")
    if dupes:
        errors.append(f"Duplicate request_ids in output: {dupes[:10]}")

    req_amounts = dict(zip(requests_df[rid_col].astype(str), requests_df[amt_col].astype(float)))

    for _, row in out.iterrows():
        rid = row["request_id"]
        try:
            amount = float(row["amount_safe_to_pay"])
        except (TypeError, ValueError):
            errors.append(f"{rid}: amount_safe_to_pay is not numeric ({row['amount_safe_to_pay']!r})")
            continue
        requested = req_amounts.get(rid)
        if requested is not None and not (0 - 1e-6 <= amount <= requested + 1e-6):
            errors.append(f"{rid}: amount_safe_to_pay={amount} violates 0<=x<={requested}")

        if row["affordability_status"] not in settings.AFFORDABILITY_STATUSES:
            errors.append(f"{rid}: invalid affordability_status {row['affordability_status']!r}")
        if row["recommended_payment_method"] not in settings.PAYMENT_METHODS:
            errors.append(f"{rid}: invalid recommended_payment_method {row['recommended_payment_method']!r}")

        plan = row["payment_plan"]
        if plan != "none":
            entries = plan.split("|")
            if not all(PAYMENT_PLAN_RE.match(e) for e in entries):
                errors.append(f"{rid}: malformed payment_plan {plan!r}")
            dates = [e.split(":")[0] for e in entries]
            if dates != sorted(dates):
                errors.append(f"{rid}: payment_plan not chronological {plan!r}")
            if row["recommended_payment_method"] == "partial_payment":
                if len(entries) != 2:
                    errors.append(f"{rid}: partial_payment must have exactly 2 payments, got {len(entries)}")
                else:
                    total = sum(float(e.split(":")[1]) for e in entries)
                    if requested is not None and abs(total - requested) > 0.01:
                        errors.append(f"{rid}: partial_payment total {total} != requested_amount {requested}")

        changes = row["spending_changes_needed"]
        if changes != "none":
            entries = changes.split("|")
            if len(entries) > settings.MAX_SPENDING_CHANGES:
                errors.append(f"{rid}: more than {settings.MAX_SPENDING_CHANGES} spending changes: {changes!r}")
            if not all(SPENDING_CHANGE_RE.match(e) for e in entries):
                errors.append(f"{rid}: malformed spending_changes_needed {changes!r}")
            event_ids = [e.split(":")[1] for e in entries]
            if len(event_ids) != len(set(event_ids)):
                errors.append(f"{rid}: same event_id targeted by multiple spending changes (stop/reduce_to must be exclusive): {changes!r}")

    # Cross-check affordable_now rows against request_date directly (needs
    # the join back to requests.csv, done here rather than row-by-row above).
    req_dates = dict(zip(requests_df[rid_col].astype(str), requests_df[date_col].astype(str)))
    for _, row in out.iterrows():
        if row["affordability_status"] == "affordable_now":
            rid = row["request_id"]
            if row["earliest_date_for_full_payment"] != req_dates.get(rid):
                errors.append(
                    f"{rid}: affordable_now requires earliest_date_for_full_payment == request_date "
                    f"({req_dates.get(rid)}), got {row['earliest_date_for_full_payment']!r}"
                )

    return errors


def main() -> int:
    data = load_all(settings.DATASET_DIR)
    errors = validate(settings.OUTPUT_PATH, data["requests"])
    if errors:
        print(f"FAILED — {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK — {settings.OUTPUT_PATH} passed all structural checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
