#!/usr/bin/env python3
"""
Runs the pipeline on dataset/sample_requests.csv (which already has the
expected output columns filled in) and diffs our predictions against them.
This is the closest thing we have to a scorer before submission — use it to
catch systematic bugs (wrong sign conventions, off-by-one on dates, wrong
tie-break ordering) before spending tokens on the full 250-row run.

Run with:
    python3 code/evaluation/score_samples.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()  # must run before `from config import settings` reads the env

from config import settings
from ingest.currency import RateTable
from ingest.loaders import load_all
from ingest.schema import CANDIDATES, resolve_column
from pipeline import process_request
from resolve.cache import DiskCache
from usage_tracker import UsageTracker


def main() -> int:
    data = load_all(settings.DATASET_DIR)
    samples = data.get("sample_requests")
    if samples is None:
        print("dataset/sample_requests.csv not found — nothing to score.")
        return 1

    rate_table = RateTable(data["exchange_rates"])
    tracker = UsageTracker()
    cache = DiskCache(Path(__file__).parent.parent / ".cache")

    rc = CANDIDATES["requests"]
    id_col = resolve_column(samples, rc["request_id"])

    # sample_requests.csv = requests.csv columns + the 8 output columns.
    # We feed it back through process_request using only the input columns,
    # then compare against the output columns already present in the file.
    input_cols = [
        resolve_column(samples, rc[k])
        for k in ["request_id", "user_id", "request_date", "request_type", "requested_amount", "desired_completion_date", "allows_partial_payment", "request_text"]
    ]

    matches = {col: 0 for col in settings.OUTPUT_COLUMNS if col != "request_id"}
    total = 0
    mismatches = []

    for _, row in samples.iterrows():
        input_row = row[input_cols]
        input_row.index = ["request_id", "user_id", "request_date", "request_type", "requested_amount", "desired_completion_date", "allows_partial_payment", "request_text"]
        try:
            predicted = process_request(input_row, data, rate_table, tracker, cache)
        except Exception as exc:
            mismatches.append((row[id_col], "PIPELINE ERROR", str(exc)))
            continue

        total += 1
        for col in matches:
            expected = str(row.get(col, "")).strip()
            actual = str(predicted.get(col, "")).strip()
            is_match = expected == actual
            if not is_match and col == "amount_safe_to_pay":
                try:
                    is_match = abs(float(expected) - float(actual)) < 0.01
                except ValueError:
                    pass
            if is_match:
                matches[col] += 1
            else:
                mismatches.append((row[id_col], col, f"expected={expected!r} actual={actual!r}"))

    print(f"Scored {total} sample requests.\n")
    print("Field-level exact-match rate:")
    for col, n in matches.items():
        pct = (n / total * 100) if total else 0
        print(f"  {col:32s} {n}/{total} ({pct:.0f}%)")

    if mismatches:
        print(f"\nFirst {min(20, len(mismatches))} mismatches:")
        for rid, field, detail in mismatches[:20]:
            print(f"  [{rid}] {field}: {detail}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
