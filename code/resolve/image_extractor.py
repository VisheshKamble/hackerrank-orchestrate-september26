"""
Extracts a monetary amount from a financial-document image, for
financial_events.csv rows whose `amount` is blank. Never treats a blank as
zero — either resolves a real number from the image, or the caller drops
that event and logs why.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from config import settings
from resolve.cache import DiskCache
from resolve.llm_client import create_vision_completion, extract_json
from usage_tracker import UsageTracker

_PROMPT_PATH = Path(__file__).parent / "prompts" / "image_extract.txt"
_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


def _media_type(path: Path) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        path.suffix.lstrip(".").lower(), "image/png"
    )


def extract_amount_from_image(
    image_path: Path, tracker: Optional[UsageTracker] = None, cache: Optional[DiskCache] = None
) -> Optional[float]:
    """Returns the extracted amount, or None if it couldn't be read
    confidently (caller must NOT treat that as zero). The image's filename
    stem (e.g. "image_07" from image_07.png) is used as the cache key — it's
    exactly the image_id per problem_statement.md's naming convention."""
    if not image_path.exists():
        return None  # missing file: caller handles the gap

    image_id = image_path.stem
    if cache is not None:
        cached = cache.get(image_id)
        if cached is not None:
            return cached.get("amount")

    result = create_vision_completion(
        model=settings.VISION_MODEL,
        prompt=_PROMPT,
        image_bytes=image_path.read_bytes(),
        media_type=_media_type(image_path),
        max_tokens=settings.MAX_TOKENS_STRUCTURED,
    )
    if result is None:
        return None  # dry-run / provider error: caller handles the gap; never cached

    if tracker is not None:
        tracker.record(settings.LLM_PROVIDER, settings.VISION_MODEL, "image_extract", result.input_tokens, result.output_tokens)

    parsed = extract_json(result.text)
    if not parsed or parsed.get("amount") is None or parsed.get("confidence") == "low":
        return None  # low-confidence / unparsable: never cached, so a retry can try again later

    try:
        amount = float(parsed["amount"])
    except (TypeError, ValueError):
        return None

    if cache is not None:
        cache.put(image_id, {"amount": amount})
    return amount


def extract_all_image_amounts(
    event_id_to_image_path: Dict[str, Path],
    tracker: Optional[UsageTracker] = None,
    cache: Optional[DiskCache] = None,
) -> Dict[str, float]:
    """event_id_to_image_path: {event_id: /path/to/dataset/media/images/imageXX.png}"""
    out = {}
    for event_id, path in event_id_to_image_path.items():
        amount = extract_amount_from_image(path, tracker, cache)
        if amount is not None:
            out[event_id] = amount
    return out
