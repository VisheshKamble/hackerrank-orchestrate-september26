"""
Thin, provider-agnostic wrapper around whichever LLM/VLM provider is active
(config.settings.LLM_PROVIDER — "groq" by default, "anthropic" supported too),
shared by image_extractor.py, message_parser.py, and explain/narrator.py.

Every call site gets back a plain CompletionResult(text, input_tokens,
output_tokens) regardless of provider, so nothing outside this file needs to
know that Groq's SDK is OpenAI-shaped (choices[0].message.content,
usage.prompt_tokens/completion_tokens) while Anthropic's is content-block
shaped (content[].text, usage.input_tokens/output_tokens).

If no API key is set for the active provider, `client()` returns None and
callers fall back to rule-based heuristics (see each module) — this keeps
the pipeline runnable end to end for development/testing without a key,
while making it obvious in usage_report.md ("0 model calls") that a dry run
happened.

Rate limiting: Groq's per-key rate limits are easy to hit on a 250-row run
with several calls per request, especially once main.py processes requests
concurrently. Two independent mitigations, both here rather than at the
call sites:
  - `_LLM_SEMAPHORE` caps how many requests are in flight to the provider
    at once, separate from (and typically smaller than) main.py's
    request-level worker count — most of a request's work is local
    computation, only the actual API call needs throttling.
  - `_with_retry` retries a 429/rate-limit error with exponential backoff
    (+ jitter), honoring a `Retry-After` header when the SDK exposes one.
    Only rate-limit-shaped errors are retried; anything else fails fast so
    a real bug doesn't masquerade as "the model is just slow".
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

from config import settings

_client = None
_client_checked = False

_LLM_SEMAPHORE = threading.Semaphore(int(os.environ.get("BOW_LLM_CONCURRENCY", "3")))
_MAX_RETRIES = int(os.environ.get("BOW_LLM_MAX_RETRIES", "5"))
_BASE_DELAY_SECONDS = 1.0
_MAX_DELAY_SECONDS = 30.0


def _is_rate_limit_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "toomanyrequests" in name:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    """Best-effort extraction of a Retry-After hint from either SDK's
    exception shape; falls back to None (caller uses backoff instead)."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers:
        for key in ("retry-after", "Retry-After"):
            value = headers.get(key) if hasattr(headers, "get") else None
            if value:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    return None


def _with_retry(call):
    """Runs `call()` (a zero-arg callable making one API request), retrying
    on rate-limit errors with exponential backoff + jitter. Any other
    exception propagates immediately — only rate limiting is worth waiting
    out here; every other error is handled by the caller's existing
    try/except-then-fallback contract."""
    attempt = 0
    while True:
        delay = None
        with _LLM_SEMAPHORE:
            try:
                return call()
            except Exception as exc:
                if not _is_rate_limit_error(exc) or attempt >= _MAX_RETRIES:
                    raise
                delay = _retry_after_seconds(exc)
        attempt += 1
        if delay is None:
            delay = min(_MAX_DELAY_SECONDS, _BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)  # jitter, avoids a thundering herd on retry
        time.sleep(delay)


@dataclass
class CompletionResult:
    text: str
    input_tokens: int
    output_tokens: int


def client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    if settings.dry_run():
        _client = None
        return None
    try:
        if settings.LLM_PROVIDER == "groq":
            from groq import Groq  # imported lazily so the package is optional in dry-run mode

            _client = Groq(api_key=settings.GROQ_API_KEY)
        elif settings.LLM_PROVIDER == "anthropic":
            import anthropic

            _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        else:
            raise ValueError(f"Unknown BOW_LLM_PROVIDER={settings.LLM_PROVIDER!r} (expected 'groq' or 'anthropic')")
    except Exception:
        _client = None
    return _client


def create_text_completion(model: str, prompt: str, max_tokens: int) -> Optional[CompletionResult]:
    """Single-turn, text-only completion. Returns None in dry-run mode or on
    any client error — callers must have a rule-based fallback."""
    c = client()
    if c is None:
        return None
    try:
        if settings.LLM_PROVIDER == "anthropic":
            response = _with_retry(
                lambda: c.messages.create(
                    model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
                )
            )
            text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            return CompletionResult(text, response.usage.input_tokens, response.usage.output_tokens)
        else:  # groq (OpenAI-compatible chat completions)
            response = _with_retry(
                lambda: c.chat.completions.create(
                    model=model, max_completion_tokens=max_tokens, messages=[{"role": "user", "content": prompt}]
                )
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return CompletionResult(
                text, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
            )
    except Exception:
        return None


def create_vision_completion(
    model: str, prompt: str, image_bytes: bytes, media_type: str, max_tokens: int
) -> Optional[CompletionResult]:
    """Single-image + text completion. Returns None in dry-run mode or on any
    client error — callers must never treat that as a resolved amount of 0."""
    c = client()
    if c is None:
        return None
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    try:
        if settings.LLM_PROVIDER == "anthropic":
            response = _with_retry(
                lambda: c.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                )
            )
            text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            return CompletionResult(text, response.usage.input_tokens, response.usage.output_tokens)
        else:  # groq (OpenAI-compatible image_url with a base64 data URL)
            response = _with_retry(
                lambda: c.chat.completions.create(
                    model=model,
                    max_completion_tokens=max_tokens,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                            ],
                        }
                    ],
                )
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return CompletionResult(
                text, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
            )
    except Exception:
        return None


def extract_json(text: str) -> Optional[dict]:
    """Models are instructed to return JSON-only, but this strips accidental
    markdown fences defensively before parsing."""
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None
