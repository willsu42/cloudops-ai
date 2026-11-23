"""Parsing and chunking: pure functions, no database."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from app.rag.chunking import (
    CHUNKER_VERSION,
    DEFAULT_MAX_CODE_TOKENS,
    DEFAULT_MAX_TOKENS,
    chunk_markdown,
    estimate_tokens,
)
from app.rag.parsing import parse_markdown
from tests.conftest import DATASETS_DIR

CORPUS = sorted((DATASETS_DIR / "corpus" / "otel-demo").glob("*.md"))

DOC = """---
title: Checkout Service
linkTitle: Checkout
---

Intro paragraph about checkout.

## Traces

### Initializing Tracing

Tracing text.

```go
# not a heading inside code
func main() {}
```

{{< tabpane text=true >}} {{% tab Make %}}

<!-- markdownlint-disable -->

## Metrics

Metrics text.
"""


def test_front_matter_shortcodes_and_comments_are_removed() -> None:
    parsed = parse_markdown(DOC)
    assert parsed.title == "Checkout Service"
    joined = "\n".join(b.text for b in parsed.blocks)
    assert "linkTitle" not in joined
    assert "{{" not in joined
    assert "markdownlint" not in joined


def test_headings_inside_code_are_not_headings() -> None:
    titles = [b.title for b in parse_markdown(DOC).blocks if b.kind == "heading"]
    assert titles == ["Traces", "Initializing Tracing", "Metrics"]


def test_heading_paths_and_line_ranges() -> None:
    chunks = chunk_markdown(DOC)
    paths = [c.heading_path for c in chunks]
    assert paths == [
        ("Checkout Service",),
        ("Checkout Service", "Traces", "Initializing Tracing"),
        ("Checkout Service", "Metrics"),
    ]
    lines = DOC.splitlines()
    for chunk in chunks:
        # The chunk's first and last lines are the source lines it claims.
        assert chunk.text.splitlines()[0].strip() == lines[chunk.start_line - 1].strip()
        assert chunk.text.splitlines()[-1].strip() == lines[chunk.end_line - 1].strip()


def test_empty_parent_section_is_dropped_but_kept_in_path() -> None:
    # "## Traces" has no content of its own, only a subsection.
    assert all(c.text.strip() != "## Traces" for c in chunk_markdown(DOC))


def test_code_block_is_never_cut_when_it_fits() -> None:
    code = "```python\n" + "\n".join(f"x{i} = {i}  # line" for i in range(60)) + "\n```"
    source = f"# Doc\n\nShort intro.\n\n{code}\n\nOutro text.\n"
    assert estimate_tokens(code) > 200
    chunks = chunk_markdown(source, max_tokens=200, overlap_tokens=20, max_code_tokens=1000)
    holders = [c for c in chunks if "x0 = 0" in c.text]
    assert len(holders) == 1
    assert code in holders[0].text


def test_oversized_code_is_split_on_lines_and_refenced() -> None:
    code = "```go\n" + "\n".join(f"fmt.Println({i}) // padding text" for i in range(200)) + "\n```"
    chunks = chunk_markdown(f"# Doc\n\n{code}\n", max_tokens=200, overlap_tokens=20)
    assert estimate_tokens(code) > DEFAULT_MAX_CODE_TOKENS
    pieces = [c for c in chunks if "fmt.Println" in c.text]
    assert len(pieces) > 1
    for piece in pieces:
        body = piece.text.split("\n", 1)[1] if piece.text.startswith("# Doc") else piece.text
        assert body.count("```") == 2, "each piece must be a complete fenced block"
    printed = [
        int(line.split("(")[1].split(")")[0])
        for c in pieces
        for line in c.text.splitlines()
        if line.startswith("fmt.Println(")
    ]
    assert printed == list(range(200)), "no line lost or duplicated"


def test_oversized_paragraph_is_split_within_limit() -> None:
    paragraph = "\n".join(f"Sentence number {i} about checkout behaviour." for i in range(120))
    chunks = chunk_markdown(f"# Doc\n\n{paragraph}\n", max_tokens=150, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(c.token_estimate <= 150 + estimate_tokens("# Doc\n\n") for c in chunks)


def test_overlap_stays_within_a_section() -> None:
    paras = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(12))
    source = f"# A\n\n{paras}\n\n# B\n\nOnly paragraph in B.\n"
    chunks = chunk_markdown(source, max_tokens=150, overlap_tokens=60)
    a_chunks = [c for c in chunks if c.heading_path == ("A",)]
    assert len(a_chunks) > 1
    for prev, nxt in itertools.pairwise(a_chunks):
        last_para = prev.text.split("\n\n")[-1]
        assert nxt.text.startswith(last_para), "consecutive chunks share trailing text"
    b_chunk = next(c for c in chunks if c.heading_path == ("B",))
    assert "Paragraph" not in b_chunk.text, "no overlap across sections"


def test_chunking_is_deterministic_and_versioned() -> None:
    first, second = chunk_markdown(DOC), chunk_markdown(DOC)
    assert first == second
    assert [c.index for c in first] == list(range(len(first)))
    assert all(c.chunker_version == CHUNKER_VERSION for c in first)


def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_markdown(DOC, max_tokens=100, overlap_tokens=100)
    with pytest.raises(ValueError):
        chunk_markdown(DOC, max_tokens=500, max_code_tokens=100)


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: Path(p).name)
def test_real_corpus_chunks_are_well_formed(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    chunks = chunk_markdown(source)
    n_lines = len(source.splitlines())
    assert chunks
    for c in chunks:
        assert c.text.strip()
        assert 1 <= c.start_line <= c.end_line <= n_lines
        assert "{{<" not in c.text and "{{%" not in c.text
        assert c.text.count("```") % 2 == 0, "no half-open code fence"
        limit = DEFAULT_MAX_CODE_TOKENS if "```" in c.text else DEFAULT_MAX_TOKENS
        assert c.token_estimate <= limit + 50  # heading line may add a little


def test_architecture_service_diagram_is_one_chunk() -> None:
    source = (DATASETS_DIR / "corpus" / "otel-demo" / "architecture.md").read_text()
    holders = [c for c in chunk_markdown(source) if "checkout -->|gRPC| currency" in c.text]
    assert len(holders) == 1
    assert "frontend -->|gRPC| checkout" in holders[0].text
