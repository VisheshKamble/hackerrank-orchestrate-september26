"""
Load every dataset/*.csv into pandas DataFrames with normalized dtypes.

Nothing in dataset/ is ever written to. All parsing here is defensive:
dates are coerced, booleans are normalized from "true"/"false" strings,
and column access always goes through ingest.schema.resolve_column so a
header mismatch fails loudly instead of silently.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from config import settings
from ingest.schema import CANDIDATES, resolve_column


def _read_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    return pd.read_csv(path)


def _coerce_bool(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .map({"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False})
    )


def _coerce_flexibility(series: pd.Series) -> pd.Series:
    """Handles both conventions seen across dataset variants: a plain
    boolean-style column (true/false/1/0/yes/no) and the real dataset's
    category-style `flexibility` column ("flexible" / "protected", and
    possibly others we haven't seen). Defaults to False (protected) for
    anything unrecognized: a false negative here just means we miss a
    legitimate spending-change option; a false positive risks the engine
    suggesting a change to something essential/protected, which is the
    worse mistake. Verify the real value set with `--schema-report` and
    extend the keyword lists below if it turns up anything not covered."""

    def _one(v) -> bool:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return False
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
        if any(k in s for k in ("flex", "discretion", "optional", "adjustable")):
            return True
        if any(k in s for k in ("protect", "essential", "fixed", "mandatory", "non_flex", "nonflexible", "core")):
            return False
        return False  # unrecognized -> conservative default (treat as protected)

    return series.map(_one)


def _coerce_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def load_all(dataset_dir: Path = settings.DATASET_DIR) -> Dict[str, pd.DataFrame]:
    """Load and lightly normalize every input file. Missing optional files are omitted."""
    raw = {
        "requests": _read_csv(dataset_dir / "requests.csv"),
        "sample_requests": _read_csv(dataset_dir / "sample_requests.csv"),
        "financial_profiles": _read_csv(dataset_dir / "financial_profiles.csv"),
        "financial_events": _read_csv(dataset_dir / "financial_events.csv"),
        "exchange_rates": _read_csv(dataset_dir / "exchange_rates.csv"),
        "request_payment_options": _read_csv(dataset_dir / "request_payment_options.csv"),
        "messages": _read_csv(dataset_dir / "messages.csv"),
        "images": _read_csv(dataset_dir / "images.csv"),
    }

    if raw["requests"] is None:
        raise FileNotFoundError(
            f"dataset/requests.csv not found under {dataset_dir}. "
            f"Set BOW_DATASET_DIR if your dataset/ folder lives elsewhere."
        )

    data: Dict[str, pd.DataFrame] = {}

    # requests.csv (required)
    df = raw["requests"].copy()
    c = CANDIDATES["requests"]
    date_col = resolve_column(df, c["request_date"], context="requests.csv")
    df[date_col] = _coerce_date(df[date_col])
    dcd_col = resolve_column(df, c["desired_completion_date"], context="requests.csv")
    df[dcd_col] = _coerce_date(df[dcd_col])
    apb_col = resolve_column(df, c["allows_partial_payment"], context="requests.csv")
    df[apb_col] = _coerce_bool(df[apb_col])
    data["requests"] = df

    # sample_requests.csv (optional — used only by evaluation/score_samples.py)
    # Same INPUT columns as requests.csv (request_date, desired_completion_date,
    # allows_partial_payment) — must get identical coercion, or process_request()
    # breaks the moment it does date arithmetic on a raw string.
    if raw["sample_requests"] is not None:
        df = raw["sample_requests"].copy()
        c = CANDIDATES["requests"]
        date_col = resolve_column(df, c["request_date"], context="sample_requests.csv")
        df[date_col] = _coerce_date(df[date_col])
        dcd_col = resolve_column(df, c["desired_completion_date"], context="sample_requests.csv")
        df[dcd_col] = _coerce_date(df[dcd_col])
        apb_col = resolve_column(df, c["allows_partial_payment"], context="sample_requests.csv")
        df[apb_col] = _coerce_bool(df[apb_col])
        data["sample_requests"] = df

    # financial_profiles.csv
    if raw["financial_profiles"] is not None:
        data["financial_profiles"] = raw["financial_profiles"].copy()
    else:
        raise FileNotFoundError("dataset/financial_profiles.csv not found — required.")

    # financial_events.csv
    if raw["financial_events"] is not None:
        df = raw["financial_events"].copy()
        c = CANDIDATES["financial_events"]
        date_col = resolve_column(df, c["event_date"], context="financial_events.csv")
        df[date_col] = _coerce_date(df[date_col])
        settle_col = resolve_column(df, c["settlement_date"], required=False, context="financial_events.csv")
        if settle_col:
            df[settle_col] = _coerce_date(df[settle_col])
        rec_col = resolve_column(df, c["is_recurring"], required=False, context="financial_events.csv")
        if rec_col:
            df[rec_col] = _coerce_bool(df[rec_col])
        flex_col = resolve_column(df, c["is_flexible"], required=False, context="financial_events.csv")
        if flex_col:
            df[flex_col] = _coerce_flexibility(df[flex_col])
        data["financial_events"] = df
    else:
        raise FileNotFoundError("dataset/financial_events.csv not found — required.")

    # exchange_rates.csv
    if raw["exchange_rates"] is not None:
        df = raw["exchange_rates"].copy()
        c = CANDIDATES["exchange_rates"]
        date_col = resolve_column(df, c["rate_date"], context="exchange_rates.csv")
        df[date_col] = _coerce_date(df[date_col])
        data["exchange_rates"] = df
    else:
        data["exchange_rates"] = pd.DataFrame(
            columns=["rate_date", "from_currency", "to_currency", "rate"]
        )

    # request_payment_options.csv
    if raw["request_payment_options"] is not None:
        df = raw["request_payment_options"].copy()
        c = CANDIDATES["request_payment_options"]
        date_col = resolve_column(df, c["start_date"], context="request_payment_options.csv")
        df[date_col] = _coerce_date(df[date_col])
        data["request_payment_options"] = df
    else:
        data["request_payment_options"] = pd.DataFrame()

    # messages.csv
    if raw["messages"] is not None:
        df = raw["messages"].copy()
        c = CANDIDATES["messages"]
        date_col = resolve_column(df, c["message_date"], required=False, context="messages.csv")
        if date_col:
            df[date_col] = _coerce_date(df[date_col])
        data["messages"] = df
    else:
        data["messages"] = pd.DataFrame()

    # images.csv
    data["images"] = raw["images"].copy() if raw["images"] is not None else pd.DataFrame()

    return data
