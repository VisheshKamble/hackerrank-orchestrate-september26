# HackerRank Orchestrate: Buy or Wait? Status Report

## 1. Challenge Summary

The challenge is to build an AI-powered financial decision agent. For every row in `dataset/requests.csv`, the system must decide whether a user should:

- pay in full now
- pay partially
- use installments
- wait and pay later
- not proceed

The decision must reconstruct the user's financial position from profiles, financial events, fixed exchange rates, payment options, messages, and images. The recommendation is safe only when every planned payment can be made, essential commitments remain covered, and the balance never falls below the user's minimum preferred balance during the 90-day forecast.

The important design principle is deterministic financial reasoning. Models may help interpret unstructured messages and images or write explanations, but they should not independently invent the balance calculation or payment plan.

## 2. Required Deliverables

The challenge requires:

1. `code.zip`: the complete runnable solution, including `evaluation/usage_report.md`
2. `output.csv`: exactly one prediction for every request in `dataset/requests.csv`
3. `chat_transcript`: the development and usage conversation transcript

The submission must not contain API keys, credentials, `.env` files, or other sensitive configuration.

## 3. Required Output Contract

`output.csv` must contain these columns in this exact order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

Important invariants include:

- `0 <= amount_safe_to_pay <= requested_amount`
- `affordability_now` uses `full_payment` and has the request date as the earliest full-payment date
- installment schedules must match a supplied payment option
- partial payment must have exactly two payments whose amounts sum to the requested amount
- spending changes may only affect permitted flexible recurring expenses
- payment dates must be chronological
- `not_recommended` must use `payment_plan=none`

## 4. What Has Been Completed

### Project implementation

- Built a deterministic 90-day balance simulator.
- Added safe amount and earliest safe full-payment calculations.
- Added payment-plan candidate generation and ranking using the challenge's six tie-break rules.
- Added conflict resolution for cancellation, settlement, amendment, source priority, and lifecycle links.
- Added foreign-currency conversion using the supplied dated exchange rates.
- Added Groq text and vision integration for message interpretation, image amount extraction, and explanations.
- Added deterministic fallbacks when an API key is unavailable.
- Added defensive CSV schema resolution.
- Mapped the real dataset headers, including:
  - `current_available_balance`
  - `flexibility`
  - `settlement_date`
  - `first_payment_date`
  - `payment_frequency_days`
  - `payment_amount`
  - `sent_at`
- Added conservative recurrence detection for repeated financial events.
- Added handling for one-payment options with blank payment frequency.
- Normalized sample request dates before sample scoring.

### Verification completed

- Dependencies installed successfully.
- Groq API calls completed successfully, with automatic retries for intermittent rate limits.
- Full run completed for all 250 requests.
- `output.csv` contains 250 data rows.
- Structural output validation passed.
- The unit suite currently has 40 passing tests.
- `code.zip` was built without `.env` and Python cache directories.
- `evaluation/usage_report.md` is included in the archive and records provider, model, calls, tokens, and estimated cost.

### Current output distribution

The verified full run produced:

| Affordability status | Rows |
|---|---:|
| `affordable_now` | 60 |
| `affordable_with_plan` | 85 |
| `affordable_later` | 8 |
| `not_affordable` | 97 |
| **Total** | **250** |

Payment methods:

| Payment method | Rows |
|---|---:|
| `full_payment` | 60 |
| `installments` | 84 |
| `partial_payment` | 1 |
| `wait` | 8 |
| `not_recommended` | 97 |

## 5. Current Assessment

### Engineering quality: 8/10

The architecture is explainable, modular, testable, and safe by default. The deterministic-first approach is appropriate for a financial decision challenge. The project also has clear input validation, a usable README, and reproducible terminal commands.

### Prediction quality: approximately 6/10

The solved examples are useful diagnostics, but the observed field-level matches remain uneven. Earlier sample scoring showed the largest gaps in:

- `amount_safe_to_pay`
- `earliest_date_for_full_payment`
- payment-plan selection

The main risk is financial-state reconstruction and forecast timing, not the Groq explanation text. A structurally valid CSV can still receive a low score if settlement timing, recurrence, pending records, or flexible spending are interpreted incorrectly.

### Overall readiness: approximately 7/10

The project is runnable and submission-ready from a packaging perspective. It is not yet a realistic 10/10 prediction system because hidden-test accuracy is not proven and the solved-example accuracy still identifies material ledger and forecast risks, although the final pass improved payment-plan, affordability, and payment-method agreement.

## 6. Highest-Priority Improvements

### Priority 1: Validate cash-flow timing

Use `settlement_date` consistently for when money changes the balance. Keep `event_date` as the record or transaction date where appropriate. Verify all statuses:

- settled debits and credits count at settlement
- pending debits are reserved conservatively
- pending credits do not count
- failed and cancelled records do not count
- unrealized investment values do not count as available cash

Add tests for salary, pending debit, pending credit, cancellation, and a settlement date different from the event date.

### Priority 2: Improve recurrence inference

The real event schema does not provide explicit recurrence flags. Detect recurrence only when history supports it. A high-confidence rule should require:

- at least three events
- same user
- same category
- same direction
- closely matching amounts
- consistent date gaps, such as monthly or quarterly
- no cancellation or lifecycle conflict

Test monthly, weekly, quarterly, irregular, and amount-changing histories. Do not infer a future commitment from only one or two events.

### Priority 3: Parse flexibility and profile permissions exactly

The profile separates protected, reducible, and stoppable categories. Event-level flexibility is categorical, not necessarily a boolean. Ensure that:

- protected categories can never be changed
- only permitted categories are changed
- `reducible` events produce `reduce_to` actions
- `stoppable` events produce `stop` actions
- a stop and reduction are never applied to the same event
- at most three changes are returned

### Priority 4: Maintain bounded spending-change search

The implementation now uses a bounded deterministic search over permitted flexible events. It covers stop/reduce combinations across a limited candidate set, preserving practical coverage while avoiding unbounded combinatorial growth.

### Priority 5: Strengthen payment-plan validation

Before writing any row, run a final validator that checks:

- every payment is inside the forecast window or permitted completion window
- every payment is safe after all prior payments
- installment dates and amounts exactly match the selected supplied option
- total installment cost is correct
- partial payments sum exactly to the requested amount
- the plan completes by `desired_completion_date`
- the selected method is accepted by the user's profile

If a candidate fails validation, discard it and select the next eligible candidate.

### Priority 6: Re-score samples after every financial change

Use this loop:

```text
edit one financial rule
run focused unit tests
run score_samples.py without API calls
inspect field-level changes
keep only changes that improve or clearly fix a rule
```

Do not spend another full Groq run until the sample scorer and structural validator are clean. The samples are the only visible accuracy signal before hidden evaluation.

### Priority 7: Make Groq use reliable and economical

The full run experienced repeated 429 responses. Improve operational reliability with:

- caching keyed by message/image/request content
- exponential backoff with jitter
- a clear retry limit
- logging failures without logging secrets or full sensitive prompts
- deterministic fallback when a model call fails
- fewer explanation calls when a deterministic explanation is sufficient

The model should remain outside the core affordability calculation.

### Priority 8: Finalize usage-report compliance

Update the placeholder model prices in `code/evaluation/usage_report.md` using the provider's current published prices. Record the source and date checked. Rebuild the report only from the final full-dataset run that produced the submitted `output.csv`.

## 7. Recommended Final Runbook

1. Run the schema report and ensure all required mappings resolve.
2. Run focused ledger and simulator tests.
3. Run all unit tests.
4. Run `score_samples.py` with the API disabled to measure deterministic behavior.
5. Fix any output formatting or invariant failures.
6. Confirm the current Groq model names are still supported.
7. Load `GROQ_API_KEY` only into the local environment.
8. Run `python code/main.py` once for the final 250-row output.
9. Run `python code/evaluation/validate_output.py`.
10. Inspect `output.csv` for row count, blank fields, invalid values, and suspicious plans.
11. Confirm `usage_report.md` describes that exact run.
12. Rebuild `code.zip` excluding `.env`, caches, and secrets.
13. Verify the archive contains `evaluation/usage_report.md`.
14. Submit `code.zip`, `output.csv`, and the chat transcript.

## 8. What Makes This Competitive

The strongest interview and judging points are:

- deterministic financial calculations instead of model-generated numbers
- explicit handling of untrusted messages and images
- conservative treatment of pending income and uncertain evidence
- transparent payment-plan ranking
- exact output validation before submission
- token usage reporting and secret hygiene
- tests that cover conflict resolution, simulation, and ranking

The strongest path to winning is accuracy first: improve the ledger and forecast semantics, prove each rule with targeted tests, and only then optimize prompts, explanations, runtime, and presentation.

## 9. Final Honest Conclusion

The project has a good, defensible foundation and is operationally ready. It should be presented as a deterministic financial reasoning engine enhanced by Groq, not as a chatbot. The next meaningful milestone is not adding more AI; it is raising solved-example accuracy through settlement-date correctness, recurrence evidence, flexible-spending search, and final plan validation. Those changes have the highest probability of improving the hidden-test score.
