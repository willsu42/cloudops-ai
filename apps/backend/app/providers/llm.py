"""Answer-drafting providers.

A provider receives the question and the retrieved passages, each with a short
label (``S1``, ``S2``, ...), and returns a JSON draft:

    {"abstained": bool, "summary": str,
     "claims": [{"text": str, "citations": ["S1", ...]}],
     "limitations": [str, ...]}

The draft is untrusted. ``app/rag/citations.py`` parses and validates it, and
drops every claim that does not cite a passage from this request (ADR 005).

Only a deterministic mock exists until AWS access is set up. A Bedrock
(Converse) provider is planned for phase 2G.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings


class LLMError(RuntimeError):
    """The provider failed to produce a draft."""


@dataclass(frozen=True)
class Evidence:
    label: str  # "S1", "S2", ... in retrieval order
    title: str
    heading_path: tuple[str, ...]
    text: str


class LLMProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    async def draft_answer(self, question: str, evidence: Sequence[Evidence]) -> str: ...


# --- Mock -------------------------------------------------------------------

_STOP_WORDS = frozenset(
    """a about after again all also an and any are as at be been before being
    between both but by can could did do does doing done during each for from
    had has have having how i if in into is it its just me more most my no nor
    not now of on once only or other our out over own same should so some such
    than that the their them then there these they this those through to too
    under until up very was we were what when where which while who whom why
    will with would you your yours""".split()
)
_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\[(])")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

MAX_CLAIMS = 3
MAX_SENTENCE_CHARS = 500
MIN_MATCHED_TERMS = 2
MIN_TERM_COVERAGE = 0.5


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def terms(text: str) -> set[str]:
    """Content words, lower-cased and crudely stemmed. Deterministic."""
    words = _WORD.findall(text.lower().replace("'s", ""))
    return {_stem(w) for w in words if len(w) >= 3 and w not in _STOP_WORDS}


def sentences(text: str) -> list[str]:
    """Prose sentences and list items of a Markdown chunk; code and headings skipped."""
    out: list[str] = []
    in_code = False
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.extend(s.strip() for s in _SENTENCE_END.split(" ".join(paragraph)) if s.strip())
            paragraph.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            flush()
            in_code = not in_code
            continue
        if in_code or stripped.startswith(("#", "|")) or not stripped:
            flush()
            continue
        if _LIST_ITEM.match(line):
            flush()
            out.append(_LIST_ITEM.sub("", line).strip())
            continue
        paragraph.append(stripped)
    flush()
    return [s for s in out if len(s) <= MAX_SENTENCE_CHARS]


class MockLLMProvider:
    """Extractive and deterministic: it can only quote, never compose.

    Each claim is one sentence copied verbatim from a passage, cited to that
    passage. A sentence qualifies only if it contains at least
    ``MIN_MATCHED_TERMS`` of the question's content terms and at least
    ``MIN_TERM_COVERAGE`` of them. If none qualifies, the draft abstains.

    The mock exercises the grounding and abstention paths. Its answers are
    quotes, not reasoning, and say nothing about a real model's quality.
    """

    model_id = "mock-extractive-v1"

    async def draft_answer(self, question: str, evidence: Sequence[Evidence]) -> str:
        wanted = terms(question)
        needed = max(MIN_MATCHED_TERMS, math.ceil(MIN_TERM_COVERAGE * len(wanted)))
        scored: list[tuple[int, int, int, str, str]] = []
        for e_index, item in enumerate(evidence):
            for s_index, sentence in enumerate(sentences(item.text)):
                matched = len(wanted & terms(sentence))
                if matched >= needed:
                    scored.append((-matched, e_index, s_index, sentence, item.label))
        scored.sort()

        claims: list[dict[str, object]] = []
        seen: set[str] = set()
        for _, _, _, sentence, label in scored:
            if sentence in seen:
                continue  # overlapping chunks can repeat a sentence
            seen.add(sentence)
            claims.append({"text": sentence, "citations": [label]})
            if len(claims) == MAX_CLAIMS:
                break

        limitation = (
            "Mock provider: claims are sentences quoted from the retrieved "
            "documentation, not a synthesized answer."
        )
        if not claims:
            draft = {
                "abstained": True,
                "summary": "",
                "claims": [],
                "limitations": [
                    "No retrieved passage addresses this question closely enough.",
                    limitation,
                ],
            }
        else:
            draft = {
                "abstained": False,
                "summary": f"{len(claims)} supporting passage(s) quoted from the documentation.",
                "claims": claims,
                "limitations": [limitation],
            }
        return json.dumps(draft)


def get_llm_provider(settings: Settings) -> LLMProvider:
    # PROVIDER_MODE=aws is rejected during configuration (see embeddings).
    if settings.provider_mode != "mock":
        raise LLMError(f"unsupported provider mode {settings.provider_mode!r}")
    return MockLLMProvider()
