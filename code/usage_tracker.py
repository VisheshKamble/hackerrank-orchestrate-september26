"""
Tracks every model call's token usage so evaluation/usage_report.md always
reflects the actual final full-dataset run that produced output.csv, rather
than being hand-estimated after the fact.
"""
from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from config.settings import MODEL_PRICES_PER_MTOK


@dataclass
class CallRecord:
    provider: str
    model: str
    purpose: str  # e.g. "image_extract", "message_classify", "explanation"
    input_tokens: int
    output_tokens: int


@dataclass
class UsageTracker:
    """Thread-safe: main.py processes requests concurrently (ThreadPoolExecutor),
    and every worker thread's LLM/VLM calls land in the same tracker instance."""

    calls: List[CallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, provider: str, model: str, purpose: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self.calls.append(CallRecord(provider, model, purpose, input_tokens, output_tokens))

    def _cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        prices = MODEL_PRICES_PER_MTOK.get(model)
        if not prices:
            return 0.0
        return (input_tokens / 1_000_000) * prices["input"] + (output_tokens / 1_000_000) * prices["output"]

    def summary(self, num_requests: int) -> str:
        by_model: Dict[str, Dict[str, int]] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0})
        for c in self.calls:
            key = f"{c.provider}/{c.model}"
            by_model[key]["calls"] += 1
            by_model[key]["in"] += c.input_tokens
            by_model[key]["out"] += c.output_tokens

        total_in = sum(c.input_tokens for c in self.calls)
        total_out = sum(c.output_tokens for c in self.calls)
        total_calls = len(self.calls)
        total_cost = sum(self._cost(c.model, c.input_tokens, c.output_tokens) for c in self.calls)

        lines = [
            "# Token Usage and Cost Report",
            "",
            f"Generated: {dt.datetime.utcnow().isoformat()}Z",
            f"Run: final full-dataset run producing `output.csv` ({num_requests} requests).",
            "",
            "## Per-model totals",
            "",
            "| Provider/Model | Calls | Input tokens | Output tokens | Est. cost (USD) |",
            "|---|---:|---:|---:|---:|",
        ]
        for key, stats in sorted(by_model.items()):
            model = key.split("/", 1)[1]
            cost = self._cost(model, stats["in"], stats["out"])
            lines.append(f"| {key} | {stats['calls']} | {stats['in']} | {stats['out']} | ${cost:.4f} |")

        avg_tokens = (total_in + total_out) / num_requests if num_requests else 0
        avg_cost = total_cost / num_requests if num_requests else 0

        lines += [
            "",
            "## Overall totals",
            "",
            f"- Total model calls: {total_calls}",
            f"- Total input tokens: {total_in}",
            f"- Total output tokens: {total_out}",
            f"- Total tokens: {total_in + total_out}",
            f"- Average tokens per request: {avg_tokens:.1f}",
            f"- Estimated total cost: ${total_cost:.4f}",
            f"- Estimated cost per request: ${avg_cost:.5f}",
            "",
            "Prices in config/settings.py (MODEL_PRICES_PER_MTOK) are Groq's published "
            "self-serve on-demand rates (groq.com/pricing), cross-checked against multiple "
            "independent pricing trackers on 2026-09-13. qwen/qwen3.6-27b is a Groq Preview-tier "
            "model — Groq's docs note preview models may be discontinued or repriced at short "
            "notice, so re-verify at https://groq.com/pricing if this report is more than a few "
            "days old.",
        ]
        return "\n".join(lines)

    def write_report(self, path: Path, num_requests: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.summary(num_requests), encoding="utf-8")
