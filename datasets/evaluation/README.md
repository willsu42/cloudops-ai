# Evaluation cases

This directory is **not** part of the retrieval corpus and must never be
ingested. It holds questions together with their expected answers.

## Files

| File | Status |
| --- | --- |
| `dev.jsonl` | 11 development cases about the OpenTelemetry Demo documentation (9 answerable, 2 insufficient-evidence). Use them freely while building and tuning. |
| `test.jsonl` | **Not created yet.** See "Held-out set" below. |

The schema is `EvaluationCase` in
[`apps/backend/app/evaluation/cases.py`](../../apps/backend/app/evaluation/cases.py).

| Field | Meaning |
| --- | --- |
| `case_id` | Unique, lowercase, stable ID. |
| `incident_group` | Groups related cases. A group may appear in only one split. |
| `split` | `dev` or `test`; must match the file name. |
| `kind` | `answerable`, `insufficient_evidence`, or `adversarial`. |
| `question` | What the user asks. |
| `expected_answer` | Reference answer for a human reviewer. |
| `relevant_source_keys` | `source_key`s from `datasets/corpus/manifest.json` that support the answer. |
| `should_abstain` | `true` when the right behaviour is to say evidence is insufficient. |
| `notes` | Labeling rationale or verification notes. |

`tests/unit/test_evaluation_cases.py` checks the schema, that every source key
exists, that IDs are unique, and that no incident group spans splits.

## Rules

1. **Group before splitting.** Assign `incident_group` first, then put whole
   groups into a split. Never divide one group across `dev` and `test`.
2. **Held-out set.** Design and freeze `test.jsonl` before any retrieval or
   prompt tuning begins (Week 3). After freezing, do not edit it in response to
   results. If test results drive a change, treat that set as development data
   and write new held-out cases for the next independent claim.
3. **No answers in the corpus.** Expected answers, root causes, fault-injection
   steps, and adversarial payloads stay out of `datasets/corpus/`.
4. **Real labels only.** Add a case only when its answer is supported by the
   current corpus (or when abstaining is the right answer). Do not add
   placeholder IDs to reach a target count.

## Not yet present

- **Checkout-failure investigation cases.** These need telemetry from a running
  demo and fault scenarios, which come in later weeks.
- **Adversarial fixtures** (prompt-injection documents, unauthorized-access
  attempts). When added, keep payload documents in a separate directory that
  only the evaluation runner reads, never under `datasets/corpus/`.
