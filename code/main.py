#!/usr/bin/env python3
"""
Entry point: reads dataset/requests.csv (+ supporting files), produces one
row per request, and writes output.csv at the repository root.

Run with:
    python3 code/main.py
    python3 code/main.py --workers 1        # sequential (easier to debug / respects strict rate limits)
    python3 code/main.py --schema-report    # print resolved column names, then exit

Environment:
    GROQ_API_KEY        set for real LLM/VLM calls via Groq (default provider —
                        message parsing, image extraction, explanation prose).
                        Without it, the pipeline still runs end to end using
                        deterministic rule-based fallbacks (see resolve/ and
                        explain/).
    ANTHROPIC_API_KEY   alternative provider — set BOW_LLM_PROVIDER=anthropic too.
    BOW_DATASET_DIR     override the dataset/ location (default: ../dataset)
    BOW_OUTPUT_PATH     override the output.csv location (default: ../output.csv)
    BOW_CACHE_DIR       override the evidence-extraction cache location
                        (default: code/.cache) — see resolve/cache.py
    BOW_WORKERS         number of requests processed concurrently (default: 6).
                        Requests are independent (each keyed by user_id), so
                        concurrency is safe; keep this modest to stay under
                        Groq's per-key rate limits on a 250-row run — raise it
                        if you're comfortably under your rate limit, lower it
                        (or use --workers 1) if you start seeing 429s.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before `from config import settings` reads the env

import pandas as pd

from config import settings
from ingest.currency import RateTable
from ingest.loaders import load_all
from ingest.schema import CANDIDATES, resolve_column, schema_report
from pipeline import process_request
from resolve.cache import DiskCache
from usage_tracker import UsageTracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("main")

DEFAULT_CACHE_DIR = Path(os.environ.get("BOW_CACHE_DIR", Path(__file__).parent / ".cache"))
DEFAULT_WORKERS = int(os.environ.get("BOW_WORKERS", "6"))


def _fallback_row(request_id: str, error: Exception) -> dict:
    """One bad row must never crash the whole run. Fall back to the safest
    possible answer and log loudly so it's easy to find and fix."""
    log.error("request_id=%s failed: %s — falling back to not_recommended", request_id, error)
    return {
        "request_id": request_id,
        "amount_safe_to_pay": 0,
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "payment_plan": "none",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "none",
        "decision_explanation": "Unable to evaluate this request due to a data-processing error; treated conservatively as not affordable.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema-report", action="store_true", help="print resolved column names per file, then exit")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--limit", type=int, default=None, help="process only the first N requests (debugging)")
    args = ap.parse_args()

    t0 = time.time()
    log.info("Loading dataset from %s", settings.DATASET_DIR)
    data = load_all(settings.DATASET_DIR)

    if args.schema_report:
        print(schema_report(data))
        return 0

    requests_df = data["requests"]
    if args.limit:
        requests_df = requests_df.head(args.limit)
    rate_table = RateTable(data["exchange_rates"])
    tracker = UsageTracker()
    cache = DiskCache(DEFAULT_CACHE_DIR)

    rc = CANDIDATES["requests"]
    id_col = resolve_column(requests_df, rc["request_id"])

    total = len(requests_df)
    rows = [None] * total
    request_ids = [str(v) for v in requests_df[id_col].tolist()]

    def _run(i: int, req_row: pd.Series) -> tuple:
        request_id = request_ids[i]
        try:
            return i, process_request(req_row, data, rate_table, tracker, cache)
        except Exception as exc:  # noqa: BLE001 - intentional: never let one row kill the run
            return i, _fallback_row(request_id, exc)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(_run, i, req_row) for i, (_, req_row) in enumerate(requests_df.iterrows())]
        for fut in as_completed(futures):
            i, row = fut.result()
            rows[i] = row
            done += 1
            if done % 25 == 0 or done == total:
                log.info("Processed %d/%d requests", done, total)

    out_df = pd.DataFrame(rows, columns=settings.OUTPUT_COLUMNS)
    settings.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(settings.OUTPUT_PATH, index=False)
    log.info("Wrote %d rows to %s", len(out_df), settings.OUTPUT_PATH)

    tracker.write_report(settings.USAGE_REPORT_PATH, num_requests=total)
    log.info("Wrote usage report to %s", settings.USAGE_REPORT_PATH)
    log.info("Done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
