# ADR 005: Grounded answers, citation validation, and abstention

- **Status:** Accepted (implemented in Week 2, phase 2E, with the mock LLM
  provider; a real model is pending AWS access)
- **Date:** 2026-11-25

## Context

`POST /api/ask` turns retrieved passages into an answer. The product promise
is evidence-grounded answers, so these must hold:

- No claim is shown without a citation to a passage that was retrieved for
  this request.
- When the evidence doesn't support an answer, the system says so. It abstains
  instead of guessing.
- A model's output is untrusted. Real models can produce invalid JSON, cite
  passages that don't exist, or make unsupported claims.

Retrieval alone cannot decide answerability. Hybrid retrieval (ADR 004)
returns candidates for almost any in-domain question, including both
`insufficient_evidence` dev cases.

Until AWS is enabled, there is no real model. The stand-in must exercise
these rules without faking support.

## Decision

### 1. Evidence is labelled per request; drafts are untrusted JSON

- Retrieved passages are labelled `S1`…`Sn` in retrieval order and given to
  the provider through `LLMProvider.draft_answer(question, evidence)`. Short
  labels are easier for models to copy correctly than UUIDs.
- The provider returns a JSON draft:
  `{abstained, summary, claims: [{text, citations: [label]}], limitations}`.
  The shape is strict: unknown fields are rejected, and lengths and counts are
  bounded.

### 2. Grounding rules, enforced in code (`app/rag/citations.py`)

1. **Malformed draft → abstain.** If the draft is not valid JSON, or not in
   the expected shape, the answer abstains with "The model's draft could not
   be validated". It is not an error response, and nothing from the draft is
   shown.
2. **A drafted abstention is honored.** Its claims and summary are discarded.
3. **Citations must come from this request.** Labels that weren't issued for
   this request are removed. A claim left with no valid citation is dropped,
   never shown uncited. A limitation reports how many claims were removed.
4. **No surviving claim → abstain.**
5. **No evidence → abstain without calling the model.** When retrieval is
   empty there is nothing to cite. `llm_model` is `null` in the response.

### 3. Responses carry everything needed to check them

The response includes:

- Each claim with its resolved citations: chunk ID, label, source key, title,
  heading path, and line range.
- **All** retrieved passages with their labels and scores.
- The models used.

Each citation resolves through `GET /api/sources/{chunk_id}` to the exact
stored text and its document version. The summary is free text and is not
trusted as evidence: the cited claims carry the evidence.

### 4. The mock LLM can only quote, never compose

`MockLLMProvider` (`mock-extractive-v1`) works like this:

- It splits each passage into prose sentences and list items, skipping code
  blocks, headings, and tables.
- It keeps a sentence only when the sentence contains at least 2 of the
  question's content terms, and at least half of them. Content terms are
  lower-cased words with stop words removed and a crude stem.
- It returns up to 3 such sentences **verbatim**, each cited to its passage,
  with a neutral summary ("N supporting passage(s) quoted…").
- If no sentence qualifies, it abstains.
- Every draft states that it comes from the mock and is quoted, not
  synthesized.

Because the mock only copies sentences from cited passages, a test can prove
that each claim appears in its cited source. The mock cannot "support" a
question by inventing text.

### 5. Errors are explicit and leak nothing

| Condition | Response |
| --- | --- |
| Empty, too long (more than 1000 characters), or extra fields | 422 |
| Blank after trimming, or unknown service | 400 with a safe message |
| Index built with another embedding model | 503 `index not ready` |
| Database failure | 503 `database unavailable` |
| Provider exception | 502 `answer generation failed` |

The question text is never logged. Only counts are logged: retrieved, claims,
dropped claims, invalid citations, and whether the answer abstained.

## Alternatives considered

- **Trust the model's citations.** Simplest, but fabricated or stale citations
  would reach users. Rejected.
- **Repair malformed drafts, or retry the model.** Repair risks inventing
  structure. A retry is reasonable with a real model, but it doubles cost and
  latency. Deferred to 2G: at most one retry for invalid JSON, then abstain.
- **Show uncited claims marked as "unsupported".** This still puts unsupported
  statements in front of users. Rejected; uncited claims are removed.
- **Decide answerability from retrieval scores alone.** ADR 004 explains why
  lexical matches over domain words can't tell answerable from unanswerable.
  Rejected as the only rule; the empty-retrieval shortcut remains.
- **A generative mock (templates that paraphrase passages).** It could look
  like a real answer, but it would produce text that isn't in any source,
  which is exactly what the rules forbid. Rejected.
- **Semantic entailment checks (NLI model or LLM judge) on every claim.**
  Stronger grounding, but it needs a model and adds cost. Planned for
  evaluation (Week 3 and later), not for the request path yet.

## Consequences

- The mock is deliberately conservative. In an ad-hoc dev run on 2026-11-25,
  both `insufficient_evidence` cases abstained, and 5 of the 9 answerable
  cases got quoted answers. The other 4 abstained because no single sentence
  covered enough question terms. **This is not a quality claim.** It shows
  only that both paths work.
- The validator checks that each claim cites a passage from this request. It
  does **not** check that the passage entails the claim. A real model could
  still cite a real passage for a claim the passage doesn't support. Showing
  every claim with its passage one click away (2F) keeps that visible until
  semantic checks exist.
- The summary is not validated. With a real model it may say more than the
  claims do. The UI should present claims and citations as the evidence and
  the summary as orientation only.
- `ask` is synchronous. Latency is retrieval plus one model call, which is
  acceptable for a local tool.

## Future extensions

- **Bedrock (2G).**
  - Converse with a system prompt that states these rules and requires JSON
    output.
  - At most one retry on a malformed draft, then abstain.
  - Botocore Stubber tests, including malformed and fabricated drafts.
- **Quote anchoring.** Ask the model for a short verbatim quote per citation,
  and verify that the quote appears in the cited chunk.
- **Entailment scoring.** Score claims with an NLI model or LLM judge in
  evaluation first; gate on the score in the request path once calibrated.
- **Evaluation metrics on dev, later on test.** Abstention precision and
  recall, citation validity rate, and share of claims supported.
