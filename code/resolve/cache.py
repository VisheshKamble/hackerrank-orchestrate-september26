"""
Disk cache for resolve/ outputs (message classification, image amount
extraction), keyed by message_id / image_id.

The same message or image can be linked to more than one request (a user's
messages.csv/images.csv rows are joined by user_id and related_event_id, not
exclusively owned by one request_id), so without caching, re-processing the
same evidence for a second request burns tokens for an answer we already
have. Caching directly serves the token-efficiency goal called out in
problem_statement.md ("batching, caching, and token efficiency").

Only REAL model outputs are cached — a dry-run/fallback result (no API key,
or a provider error) is intentionally never cached, so that setting a real
key later and re-running doesn't get stuck serving degraded answers from an
earlier keyless run.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

_WRITE_LOCK = threading.Lock()
CACHE_VERSION = "v1"  # bump if resolve/prompts/*.txt change meaningfully


class DiskCache:
    def __init__(self, cache_dir: Path):
        self.dir = cache_dir / CACHE_VERSION
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self.dir / f"{safe_key}.json"

    def get(self, key: str) -> Optional[dict]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, value: dict) -> None:
        with _WRITE_LOCK:
            self._path(key).write_text(json.dumps(value), encoding="utf-8")
