"""
Central configuration for the Buy-or-Wait solution.

Nothing sensitive lives here. API keys are read from environment
variables only (see .env.example).
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# This file lives at <repo_root>/code/config/settings.py, so repo root is
# three levels up. Resolving paths this way means the solution runs
# correctly regardless of the caller's current working directory.
CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DATASET_DIR = Path(os.environ.get("BOW_DATASET_DIR", REPO_ROOT / "dataset"))
MEDIA_IMAGES_DIR = DATASET_DIR / "media" / "images"
OUTPUT_PATH = Path(os.environ.get("BOW_OUTPUT_PATH", REPO_ROOT / "output.csv"))
EVALUATION_DIR = CODE_DIR / "evaluation"
USAGE_REPORT_PATH = EVALUATION_DIR / "usage_report.md"

# ---------------------------------------------------------------------------
# Business rules (from problem_statement.md — do not change without
# re-reading the spec, these encode graded behaviour)
# ---------------------------------------------------------------------------
FORECAST_HORIZON_DAYS = 90
MAX_SPENDING_CHANGES = 3

AFFORDABILITY_STATUSES = {
    "affordable_now",
    "affordable_with_plan",
    "affordable_later",
    "not_affordable",
}
PAYMENT_METHODS = {
    "full_payment",
    "partial_payment",
    "installments",
    "wait",
    "not_recommended",
}

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

# ---------------------------------------------------------------------------
# LLM / VLM configuration
# ---------------------------------------------------------------------------
# Provider is swappable: "groq" (default) or "anthropic". All model calls are
# optional regardless of provider — if no API key is set for the selected
# provider, resolve/ and explain/ fall back to deterministic, rule-based
# behaviour so the pipeline still runs end to end (with reduced quality on
# unstructured extraction and explanation prose). For the graded run you
# must set a real key.
LLM_PROVIDER = os.environ.get("BOW_LLM_PROVIDER", "groq").strip().lower()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Groq's model lineup churns fast (several models were deprecated mid-2026
# after this was written) — verify these are still current at
# https://console.groq.com/docs/models and https://console.groq.com/docs/vision
# before the graded run, and override via env vars if they've moved on.
_DEFAULT_MODELS = {
    "groq": {"text": "openai/gpt-oss-120b", "vision": "qwen/qwen3.6-27b"},
    "anthropic": {"text": "claude-sonnet-5", "vision": "claude-sonnet-5"},
}
TEXT_MODEL = os.environ.get("BOW_TEXT_MODEL", _DEFAULT_MODELS[LLM_PROVIDER]["text"])
VISION_MODEL = os.environ.get("BOW_VISION_MODEL", _DEFAULT_MODELS[LLM_PROVIDER]["vision"])
MAX_TOKENS_STRUCTURED = 400
MAX_TOKENS_EXPLANATION = 200

# Per-million-token prices (USD), self-serve on-demand rate (not Batch API,
# which halves these). Verified against multiple independent sources citing
# Groq's published rate card (groq.com/pricing) — checked 2026-09-13:
#   openai/gpt-oss-120b: $0.15 / $0.60 (in/out per 1M tokens)
#   qwen/qwen3.6-27b:    $0.60 / $3.00 (in/out per 1M tokens) — Preview-tier
#     model on Groq's side; the docs note preview models "may be discontinued
#     at short notice" and are priced/positioned differently from the GA
#     catalog, so re-verify at https://groq.com/pricing before your graded
#     run if it's been more than a few days since 2026-09-13.
# claude-sonnet-5's price is Anthropic's published API rate, for the
# alternative provider path (BOW_LLM_PROVIDER=anthropic).
MODEL_PRICES_PER_MTOK = {
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "qwen/qwen3.6-27b": {"input": 0.60, "output": 3.00},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
}


def dry_run() -> bool:
    """True when no API key is configured for the active provider — pipeline
    uses rule-based fallbacks."""
    key = GROQ_API_KEY if LLM_PROVIDER == "groq" else ANTHROPIC_API_KEY
    return not key
