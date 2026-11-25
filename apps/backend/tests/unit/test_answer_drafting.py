"""Mock LLM drafting and citation grounding (pure functions, no database)."""

from __future__ import annotations

import json

from app.providers.llm import Evidence, MockLLMProvider, sentences, terms
from app.rag.citations import INVALID_DRAFT, NO_GROUNDED_CLAIMS, ground

CART = Evidence(
    label="S1",
    title="Cart Service",
    heading_path=("Cart Service",),
    text=(
        "# Cart Service\n\nThis service maintains items placed in the shopping cart by users. "
        "It interacts with a Valkey caching service for fast access to shopping cart data.\n\n"
        "```csharp\n// shopping cart data lives in Valkey\nvar cart = Get();\n```\n\n"
        "- Valkey stores the shopping cart data in memory."
    ),
)


def test_terms_drop_stop_words_and_stem() -> None:
    assert terms("Where does the cart service keep shopping carts?") == {
        "cart",
        "service",
        "keep",
        "shopp",
    }


def test_sentences_skip_code_and_headings_and_keep_list_items() -> None:
    assert sentences(CART.text) == [
        "This service maintains items placed in the shopping cart by users.",
        "It interacts with a Valkey caching service for fast access to shopping cart data.",
        "Valkey stores the shopping cart data in memory.",
    ]


async def test_mock_quotes_supporting_sentences_verbatim() -> None:
    draft = json.loads(
        await MockLLMProvider().draft_answer("Where is shopping cart data kept?", [CART])
    )
    assert draft["abstained"] is False
    texts = [c["text"] for c in draft["claims"]]
    assert texts[0] in sentences(CART.text)
    assert all(t in sentences(CART.text) for t in texts)
    assert all(c["citations"] == ["S1"] for c in draft["claims"])
    assert not any("var cart" in t for t in texts)  # code is never quoted


async def test_mock_abstains_without_enough_overlap() -> None:
    draft = json.loads(
        await MockLLMProvider().draft_answer("What is the p99 latency of PlaceOrder?", [CART])
    )
    assert draft == {
        "abstained": True,
        "summary": "",
        "claims": [],
        "limitations": [
            "No retrieved passage addresses this question closely enough.",
            "Mock provider: claims are sentences quoted from the retrieved documentation, "
            "not a synthesized answer.",
        ],
    }


def _draft(claims: list[dict[str, object]], **extra: object) -> str:
    return json.dumps(
        {"abstained": False, "summary": "s", "claims": claims, "limitations": [], **extra}
    )


def test_ground_keeps_cited_claims_and_drops_fabricated_ones() -> None:
    raw = _draft(
        [
            {"text": "valid", "citations": ["S1", "S99", "S1"]},
            {"text": "invented source", "citations": ["S99"]},
            {"text": "uncited", "citations": []},
        ]
    )
    result = ground(raw, {"S1": object()})
    assert result.abstained is False
    assert [(c.text, c.labels) for c in result.claims] == [("valid", ("S1",))]
    assert result.dropped_claims == 2
    assert result.invalid_citations == 2
    assert result.limitations == ("2 claim(s) without a valid citation were removed.",)


def test_ground_abstains_when_no_claim_survives() -> None:
    result = ground(_draft([{"text": "x", "citations": ["S7"]}]), {"S1": object()})
    assert result.abstained is True
    assert result.claims == ()
    assert NO_GROUNDED_CLAIMS in result.limitations


def test_ground_honours_a_drafted_abstention() -> None:
    raw = json.dumps(
        {
            "abstained": True,
            "summary": "ignored",
            "claims": [{"text": "ignored", "citations": ["S1"]}],
            "limitations": ["not in the docs"],
        }
    )
    result = ground(raw, {"S1": object()})
    assert (result.abstained, result.summary, result.claims) == (True, "", ())
    assert result.limitations == ("not in the docs",)


def test_ground_rejects_malformed_drafts() -> None:
    for raw in ("not json", "[]", _draft([], unexpected=1), '{"abstained": false}'):
        result = ground(raw, {"S1": object()})
        assert result.abstained is True
        assert result.limitations == (INVALID_DRAFT,)
