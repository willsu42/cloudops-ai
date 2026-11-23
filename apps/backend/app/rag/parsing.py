"""Markdown parsing for the documentation corpus.

Turns a Hugo-flavoured Markdown file into a flat list of blocks (headings,
text, and fenced code), each tagged with its 1-based line range in the original
file. Front matter is read for the title and removed. Hugo shortcodes such as
``{{< tabpane >}}`` and ``{{% tab Make %}}`` are layout markup, not content, so
they are stripped outside code blocks, as are single-line HTML comments (for
example markdownlint directives). Fenced code is kept verbatim.

This is pure text processing: no I/O, no randomness.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

BlockKind = Literal["heading", "text", "code"]

_FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
_HEADING = re.compile(r"^\s*(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_SHORTCODE = re.compile(r"\{\{[<%].*?[%>]\}\}")
_HTML_COMMENT = re.compile(r"<!--.*?-->")
_TITLE = re.compile(r"^title:\s*(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str
    start_line: int  # 1-based, inclusive, in the original file
    end_line: int  # 1-based, inclusive
    level: int = 0  # heading level (1-6); 0 for non-headings
    title: str = ""  # heading title; empty for non-headings


@dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    blocks: tuple[Block, ...]


def _strip_front_matter(lines: list[str]) -> tuple[str | None, int]:
    """Return (title, index of the first body line)."""
    if not lines or lines[0].strip() != "---":
        return None, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            title = None
            for line in lines[1:i]:
                match = _TITLE.match(line)
                if match:
                    title = match.group("title").strip("'\"")
                    break
            return title, i + 1
    return None, 0  # unterminated front matter: treat everything as body


def _clean(line: str) -> str:
    return _HTML_COMMENT.sub("", _SHORTCODE.sub("", line)).rstrip()


def parse_markdown(source: str) -> ParsedDocument:
    lines = source.splitlines()
    title, i = _strip_front_matter(lines)
    blocks: list[Block] = []
    para: list[str] = []
    para_start = 0

    def flush_paragraph(end_index: int) -> None:
        nonlocal para
        if para:
            blocks.append(Block("text", "\n".join(para), para_start, end_index))
            para = []

    while i < len(lines):
        line = lines[i]
        lineno = i + 1
        fence = _FENCE.match(line)
        if fence:
            flush_paragraph(lineno - 1)
            marker = fence.group("fence")
            close = re.compile(rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            end = min(j, len(lines) - 1)  # an unclosed fence runs to end of file
            code = "\n".join(lines[i : end + 1])
            blocks.append(Block("code", code, lineno, end + 1))
            i = end + 1
            continue

        cleaned = _clean(line)
        heading = _HEADING.match(cleaned)
        if heading:
            flush_paragraph(lineno - 1)
            blocks.append(
                Block(
                    "heading",
                    cleaned.strip(),
                    lineno,
                    lineno,
                    level=len(heading.group("marks")),
                    title=heading.group("title"),
                )
            )
        elif cleaned.strip():
            if not para:
                para_start = lineno
            para.append(cleaned)
        else:
            flush_paragraph(lineno - 1)
        i += 1

    flush_paragraph(len(lines))
    return ParsedDocument(title=title, blocks=tuple(blocks))
