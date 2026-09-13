# Buy or Wait? — Solution

An AI financial affordability agent. The core decision logic (90-day
balance forecasting, safe-amount calculation, payment-plan ranking) is a
**deterministic engine** — every graded number traces back to plain
arithmetic over the ledger, not a model's free-form judgment. LLM/VLM calls
are used only where the deterministic engine genuinely can't reach:
reading amounts out of images, interpreting free-text messages as
structured amendments, and writing the final explanation sentence.

## Why deterministic-first

`amount_safe_to_pay`, `earliest_date_for_full_payment`, and the choice
between payment plans are graded against exact values and an exact 6-rule
tie-break order (see `problem_statement.md`, "Choosing Between Safe
Plans"). Letting a model compute these directly would make them
non-reproducible and hard to defend. Instead:

- `forecast/simulator.py` builds a 90-day balance trajectory from the
  ledger, once, and derives both `amount_safe_to_pay` and
  `earliest_date_for_full_payment` from it in closed form (see the
  docstring there for the derivation).
- `plans/ranker.py` is a single sort-key tuple implementing the 6 rules
  in order — no model call.
- `resolve/` (image + message understanding) and `explain/` (final prose)
  are the only places an LLM/VLM touches the pipeline, and `explain/`
  is fed the already-computed numbers, not asked to invent them.

## First thing to do after cloning the real dataset

This solution was written from `problem_statement.md`'s prose description
of `financial_profiles.csv`, `financial_events.csv`,
`request_payment_options.csv`, `messages.csv`, and `images.csv` — only
`requests.csv` was available while writing it. Column names are therefore
best-effort guesses, resolved defensively (see `ingest/schema.py`), not
hardcoded blindly.

Run this first:

```bash
python3 code/main.py --schema-report
```

It prints which column every module resolved to, per file. Anything
showing `!! NOT FOUND !!` needs a one-line fix: add the real header to the
matching list in `ingest/schema.py` (`CANDIDATES` dict) — no other file
needs to change.

## Setup

```bash
cd code
pip install -r requirements.txt
cp .env.example .env   # then fill in GROQ_API_KEY
export GROQ_API_KEY=gsk_...
```

The provider is Groq by default (`BOW_LLM_PROVIDER=groq` in `config/settings.py`),
using `openai/gpt-oss-120b` for text and `qwen/qwen3.6-27b` for vision.
**Groq's model lineup changes often** — several models were deprecated
mid-2026 — so before your graded run, check
[console.groq.com/docs/models](https://console.groq.com/docs/models) and
[console.groq.com/docs/vision](https://console.groq.com/docs/vision) and
override `BOW_TEXT_MODEL` / `BOW_VISION_MODEL` in `.env` if either has moved
on. Anthropic is also supported (`BOW_LLM_PROVIDER=anthropic` +
`ANTHROPIC_API_KEY`) if you want to compare providers for the usage report.

Without an API key, the pipeline still runs end to end using deterministic
fallbacks for message classification (keyword matching) and explanation
generation (templates), and simply skips image-based amount recovery. This
is intentional (dev-mode / cheap smoke testing) but **do set a real key for
the graded run** — image extraction has no non-LLM fallback, since a blank
amount must never be silently treated as zero.

## Run

```bash
python3 code/main.py
```

Reads `dataset/` at the repo root, writes `output.csv` at the repo root,
and writes `code/evaluation/usage_report.md` summarizing every model call
made during that run.

## Validate before submitting

```bash
python3 code/evaluation/score_samples.py     # accuracy vs dataset/sample_requests.csv
python3 code/evaluation/validate_output.py   # structural checks on output.csv
```

`score_samples.py` is the closest thing to a scorer we have pre-submission
— it diffs our predictions against the solved examples in
`sample_requests.csv`. `validate_output.py` only catches structural
mistakes (schema, invariants, formatting) — it cannot tell you if a number
is *correct*, only that it isn't obviously malformed.

## Tests

```bash
python3 -m pytest tests/ -v
```

Unit tests use small, hand-worked synthetic ledgers (no dataset files
needed) so the expected numbers in `test_simulator.py` can be verified by
reading the comments, not by trusting the code under test.

## Module map

| Module | Responsibility |
|---|---|
| `ingest/` | CSV loading, defensive column resolution, currency conversion |
| `resolve/` | VLM image→amount, LLM message→amendment (untrusted-input boundary), disk-cached by id |
| `ledger/` | Per-user clean event table: conflict resolution, lifecycle collapsing |
| `forecast/` | 90-day balance simulation, `amount_safe_to_pay`, `earliest_date_for_full_payment` |
| `plans/` | Candidate generation, eligibility filtering, the 6-rule ranker |
| `explain/` | Turns computed facts into `decision_explanation` prose |
| `pipeline.py` | Orchestrates one request end to end |
| `main.py` | Orchestrates the full dataset run |
| `evaluation/` | `usage_report.md`, `validate_output.py`, `score_samples.py` |

## What changed in this merged/hardened pass

This version merges two independently-written attempts at the same
problem and fixes what testing turned up. For transparency (useful for
the AI Judge interview):

- **Added `resolve/cache.py`**, a disk cache keyed by `message_id`/
  `image_id`, wired into `resolve/message_parser.py` and
  `resolve/image_extractor.py`. The same message/image can be linked to
  more than one request, so this avoids paying twice for the same
  extraction — matters for the token-efficiency point in
  `problem_statement.md` and for `usage_report.md`. Only real model
  outputs are cached, never dry-run/fallback results.
- **Enforced `max_installment_months`** end to end (`ingest/schema.py` →
  `ledger/build_ledger.py` → `plans/candidates.py`), which the original
  pass didn't read at all. Per AGENTS.md, a blank value means the user
  won't do installments regardless of what's in
  `payment_methods_user_will_consider`; a non-blank value caps how long
  a plan may run. Covered by `tests/test_installment_eligibility.py`.
- **Fixed a real bug**: `ingest/loaders.py` coerced `request_date` /
  `desired_completion_date` / `allows_partial_payment` on `requests.csv`
  but not on `sample_requests.csv`, so `evaluation/score_samples.py`
  crashed on a date-minus-string error before it ever scored anything.
  Fixed by applying the identical coercion to both files.
- **`main.py` now processes requests concurrently** (`ThreadPoolExecutor`,
  default 6 workers, override with `--workers` / `BOW_WORKERS`) instead
  of sequentially — `UsageTracker` was made thread-safe to match. Each
  request only touches its own user's data, so this is safe; turn it down
  if you see 429s from Groq.
- **`.env` is now actually loaded** (`python-dotenv`, added to
  `requirements.txt`) — previously `.env.example` implied `.env` would be
  read automatically, but nothing called `load_dotenv()`.
- Verified `openai/gpt-oss-120b` and `qwen/qwen3.6-27b` are current,
  real Groq models as of this writing — the latter is officially a
  **preview** model on Groq's side (fine for a hackathon submission,
  worth a one-line mention if asked).

## What changed after the first real-data run (round 2)

These six items came from actually running against the real dataset — the
first pass above was written blind from the prose spec and missed real
schema quirks:

1. **Cash flow now uses `settlement_date`, not `event_date`**, wherever the
   real file provides it (`ledger/build_ledger.py`, `ledger/conflict_resolution.py::RawEvent.effective_date`).
   `event_date` is still what conflict-resolution's recency tie-break uses;
   `effective_date` (settlement-first) is what forecast/ and FX conversion
   use — those are genuinely different questions and were conflated before.
2. **Recurring expenses are now inferred from history**
   (`ledger/build_ledger.py::detect_recurring_from_history`), since the real
   `financial_events.csv` has no `is_recurring`/`recurrence_frequency_days`
   columns at all — previously that meant almost everything fell through to
   "one-time". Detection requires ≥3 historical occurrences in the same
   category with gaps consistent within 2x of each other, skips any
   category that already has an explicit future-dated row (avoids double-
   counting), and estimates conservatively: shortest observed gap as the
   cadence, worst-case historical amount as the projected one. Covered by
   `tests/test_recurrence_detection.py` (8 tests).
3. **Fixed flexibility parsing** — the real dataset uses a string-valued
   `flexibility` column ("flexible"/"protected"), not a boolean, which
   `_coerce_bool` silently turned into `False` for every row (neither
   string matches true/false/1/0/yes/no). New `ingest/loaders.py::_coerce_flexibility`
   handles both conventions and defaults to `False` (protected) for
   anything unrecognized — touching a protected expense is the worse
   mistake. Covered by `tests/test_flexibility_parsing.py` (5 tests).
4. Re-run `evaluation/score_samples.py` after pulling this — the
   settlement-date and recurrence-inference fixes are the ones likely to
   move the forecast-timing mismatches; nothing about Groq/explanations
   changes those numbers.
5. **Rate-limit handling in `resolve/llm_client.py`**: every provider call
   now goes through `_with_retry` (exponential backoff + jitter, honors
   `Retry-After` when the SDK exposes it, only retries genuine 429/rate-limit
   errors — anything else fails fast into the existing rule-based fallback)
   and a semaphore (`BOW_LLM_CONCURRENCY`, default 3) caps concurrent
   in-flight provider calls independent of `BOW_WORKERS`. Explanation
   generation is now cached too (`explain/narrator.py`, keyed by a hash of
   the input facts) — same decision -> same explanation, so re-running
   `score_samples.py` or `main.py` after an unrelated fix doesn't re-spend
   tokens on unchanged requests.
6. **`usage_report.md` pricing is no longer a placeholder** —
   `config/settings.py::MODEL_PRICES_PER_MTOK` now has Groq's published
   on-demand rates ($0.15/$0.60 for gpt-oss-120b, $0.60/$3.00 for
   qwen3.6-27b), cross-checked against several independent pricing trackers
   on 2026-09-13 and cited in both the config comment and the generated
   report. `qwen/qwen3.6-27b` is a Groq **Preview**-tier model — re-verify
   at groq.com/pricing if you run this more than a few days later, since
   Groq's docs warn preview pricing/availability can change without notice.

## Known simplifications (disclose these in the AI Judge interview)

- **Spending-change search is bounded**: the engine exhaustively searches
  stop/reduce combinations over the first eight flexible expenses, with at
  most three changes, to avoid unbounded combinatorial growth while covering
  practical combinations.
- **Currency conversion assumes `financial_events.csv` is the only place
  foreign currency appears** — per the spec, requests/balances/payment
  options are already in `home_currency`.
- **Column-name assumptions**: see the schema-report step above. This is
  the single highest-risk area since it wasn't tested against the real
  file headers while writing this code.
