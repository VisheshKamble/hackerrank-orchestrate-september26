"""
Classifies each message tied to a financial event into a structured
amendment action. Message text is treated as untrusted data throughout —
see prompts/message_classify.txt — never as instructions to the agent.

Without an API key (or on any provider error), falls back to a conservative
keyword matcher. The fallback is intentionally narrow: it only fires on
unambiguous keywords and returns "none" otherwise, because a wrong automatic
cancellation/amendment is worse than missing one.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from config import settings
from resolve.cache import DiskCache
from resolve.llm_client import create_text_completion, extract_json
from usage_tracker import UsageTracker

_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "message_classify.txt").read_text(encoding="utf-8")

_CANCEL_WORDS = re.compile(r"\b(cancel+ed|cancel+ation|reversed|will not (go|happen)|no longer (happening|due))\b", re.I)
_CONFIRM_WORDS = re.compile(r"\b(confirmed|settled|went through|received|paid as (scheduled|planned))\b", re.I)


def _fallback_classify(message_text: str) -> dict:
    if _CANCEL_WORDS.search(message_text):
        return {"action": "cancel", "new_value": None, "confidence": "medium"}
    if _CONFIRM_WORDS.search(message_text):
        return {"action": "confirm", "new_value": None, "confidence": "medium"}
    return {"action": "none", "new_value": None, "confidence": "low"}


def classify_message(
    message_text: str,
    tracker: Optional[UsageTracker] = None,
    cache: Optional[DiskCache] = None,
    message_id: Optional[str] = None,
) -> dict:
    if cache is not None and message_id is not None:
        cached = cache.get(message_id)
        if cached is not None:
            return cached

    prompt = _PROMPT_TEMPLATE.replace("{message_text}", message_text)
    result = create_text_completion(settings.TEXT_MODEL, prompt, settings.MAX_TOKENS_STRUCTURED)
    if result is None:
        return _fallback_classify(message_text)  # never cached — see module docstring

    if tracker is not None:
        tracker.record(settings.LLM_PROVIDER, settings.TEXT_MODEL, "message_classify", result.input_tokens, result.output_tokens)

    parsed = extract_json(result.text)
    if not parsed or parsed.get("action") not in {"cancel", "amend_amount", "amend_date", "confirm", "none"}:
        parsed = _fallback_classify(message_text)  # not cached either — see module docstring

    if cache is not None and message_id is not None:
        cache.put(message_id, parsed)
    return parsed


def build_message_actions(
    messages_for_event: list, tracker: Optional[UsageTracker] = None, cache: Optional[DiskCache] = None
) -> list:
    """messages_for_event: [{"event_id", "message_id", "message_text", "message_date"}, ...]
    Returns the action list consumed by ledger.build_ledger._apply_message_amendments."""
    actions = []
    for m in messages_for_event:
        result = classify_message(m["message_text"], tracker, cache, m.get("message_id"))
        if result["action"] == "none":
            continue
        actions.append(
            {
                "event_id": m["event_id"],
                "action": result["action"],
                "new_value": result.get("new_value"),
                "message_date": m.get("message_date"),
            }
        )
    return actions
