"""Parse an untrusted answer draft and keep only claims grounded in this request.

Rules (ADR 005):

1. A draft that is not valid JSON in the expected shape is not used. The answer
   abstains with a limitation saying the draft could not be validated.
2. If the draft abstains, its claims are ignored.
3. Each citation must be a label of a passage retrieved for *this* request.
   Unknown labels are removed. A claim left with no valid citation is dropped,
   never shown uncited.
4. If no claim survives, the answer abstains.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_CLAIMS = 8
MAX_CLAIM_CHARS = 1000
MAX_SUMMARY_CHARS = 2000
MAX_LIMITATIONS = 5


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftClaim(_Strict):
    text: str = Field(min_length=1, max_length=MAX_CLAIM_CHARS)
    citations: list[str] = Field(max_length=10)


class Draft(_Strict):
    abstained: bool
    summary: str = Field(max_length=MAX_SUMMARY_CHARS)
    claims: list[DraftClaim] = Field(max_length=MAX_CLAIMS)
    limitations: list[str] = Field(max_length=MAX_LIMITATIONS)


@dataclass(frozen=True)
class GroundedClaim:
    text: str
    labels: tuple[str, ...]  # valid, de-duplicated, in the draft's order


@dataclass(frozen=True)
class Grounded:
    abstained: bool
    summary: str
    claims: tuple[GroundedClaim, ...]
    limitations: tuple[str, ...]
    dropped_claims: int
    invalid_citations: int


INVALID_DRAFT = "The model's draft could not be validated, so no answer is given."
NO_GROUNDED_CLAIMS = "No claim in the draft cited the retrieved documentation."


def parse_draft(raw: str) -> Draft | None:
    try:
        return Draft.model_validate(json.loads(raw))
    except (ValueError, ValidationError):  # json.JSONDecodeError is a ValueError
        return None


def ground(raw: str, labels: Mapping[str, object]) -> Grounded:
    """Validate a raw draft against the labels of this request's evidence."""
    draft = parse_draft(raw)
    if draft is None:
        return Grounded(True, "", (), (INVALID_DRAFT,), 0, 0)
    limitations = tuple(x for x in draft.limitations if x)
    if draft.abstained:
        return Grounded(True, "", (), limitations, 0, 0)

    kept: list[GroundedClaim] = []
    dropped = invalid = 0
    for claim in draft.claims:
        valid = tuple(dict.fromkeys(c for c in claim.citations if c in labels))
        invalid += len(claim.citations) - sum(1 for c in claim.citations if c in labels)
        if valid:
            kept.append(GroundedClaim(claim.text, valid))
        else:
            dropped += 1

    if not kept:
        return Grounded(True, "", (), (*limitations, NO_GROUNDED_CLAIMS), dropped, invalid)
    if dropped:
        limitations = (
            *limitations,
            f"{dropped} claim(s) without a valid citation were removed.",
        )
    return Grounded(False, draft.summary, tuple(kept), limitations, dropped, invalid)
