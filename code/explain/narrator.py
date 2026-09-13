"""
Writes `decision_explanation` from already-computed facts. The numeric
decision is made entirely upstream (forecast/ + plans/) — this module is
never allowed to change it, only describe it. When no API key is
configured (or the provider call fails), a deterministic template covers
every affordability_status so the field is never empty.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from config import settings
from resolve.cache import DiskCache
from resolve.llm_client import create_text_completion
from usage_tracker import UsageTracker

_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "explain.txt").read_text(encoding="utf-8")

_TEMPLATES = {
    "affordable_now": (
        "You can safely pay the full {requested_amount} {currency} today ({request_date}) "
        "and still keep your {minimum_balance} {currency} minimum balance over the next 90 days."
    ),
    "affordable_with_plan": (
        "Paying the full {requested_amount} {currency} today isn't safe, but a {method} plan "
        "keeps you above your {minimum_balance} {currency} minimum balance and completes by "
        "{earliest_date}, ahead of your {deadline} deadline."
    ),
    "affordable_later": (
        "You can safely pay only {amount_safe_to_pay} {currency} today; the full "
        "{requested_amount} {currency} isn't safe to pay in full until {earliest_date}."
    ),
    "not_affordable": (
        "Paying {requested_amount} {currency} isn't safe within the next 90 days without "
        "breaking your {minimum_balance} {currency} minimum balance, even with the payment "
        "methods and spending changes available to you."
    ),
}


def _fallback_explanation(facts: dict) -> str:
    template = _TEMPLATES.get(facts.get("affordability_status"), _TEMPLATES["not_affordable"])
    try:
        return template.format(**facts)
    except KeyError:
        return (
            f"Based on your balance and upcoming commitments, this request is "
            f"{facts.get('affordability_status', 'not_affordable')}."
        )


def _facts_cache_key(facts: dict) -> str:
    """A stable hash of the input facts — same numeric decision -> same
    explanation, so this hits across repeated dev-loop runs (score_samples.py
    iteration, re-running main.py after an unrelated fix) even though each
    request's facts are unique within a single run."""
    canonical = json.dumps(facts, sort_keys=True, default=str)
    return "explain_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def generate_explanation(
    facts: dict, tracker: Optional[UsageTracker] = None, cache: Optional[DiskCache] = None
) -> str:
    cache_key = _facts_cache_key(facts) if cache is not None else None
    if cache is not None:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached.get("text", "")

    facts_text = "\n".join(f"- {k}: {v}" for k, v in facts.items())
    prompt = _PROMPT_TEMPLATE.replace("{facts}", facts_text)
    result = create_text_completion(settings.TEXT_MODEL, prompt, settings.MAX_TOKENS_EXPLANATION)
    if result is None:
        return _fallback_explanation(facts)  # never cached — see resolve/cache.py's module docstring

    if tracker is not None:
        tracker.record(settings.LLM_PROVIDER, settings.TEXT_MODEL, "explanation", result.input_tokens, result.output_tokens)

    text = result.text.strip()
    if not text:
        return _fallback_explanation(facts)

    if cache is not None:
        cache.put(cache_key, {"text": text})
    return text
