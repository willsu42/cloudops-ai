"""Heading-aware chunking of parsed Markdown.

Rules (deterministic):

1. Blocks are grouped into sections by heading. Each section carries its
   heading path, e.g. ("Checkout Service", "Traces", "Initializing Tracing").
   The document title from front matter is the root. Sections with no content
   are dropped, but their titles stay in their children's paths.
2. Inside a section, blocks are packed greedily up to ``max_tokens``. A chunk
   never spans two sections. A section's heading line stays attached to the
   first content after it, so it can add a few tokens to that chunk. A single
   fenced code block may exceed ``max_tokens`` (see rule 3).
3. Fenced code blocks are never cut while they fit in ``max_code_tokens``.
   A larger one is split at line boundaries, and every piece is re-fenced.
4. A text block larger than ``max_tokens`` is split at line boundaries (or at
   word boundaries for a single overlong line).
5. Consecutive chunks of the same section overlap by up to ``overlap_tokens``,
   taken from whole trailing text blocks. Code is never duplicated as overlap.

Token counts are an approximation (about 4 characters per token). They size the
chunks; they are not a model tokenizer.

Bump ``CHUNKER_VERSION`` whenever these rules or their defaults change, so
stored chunks can be re-generated.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from app.rag.parsing import Block, parse_markdown

CHUNKER_VERSION = "md-heading-v1"

DEFAULT_MAX_TOKENS = 500
DEFAULT_OVERLAP_TOKENS = 75
DEFAULT_MAX_CODE_TOKENS = 1000


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


@dataclass(frozen=True)
class Chunk:
    index: int  # position within the document, starting at 0
    text: str
    heading_path: tuple[str, ...]
    start_line: int  # 1-based, inclusive, in the original file
    end_line: int  # 1-based, inclusive
    token_estimate: int
    content_sha256: str
    chunker_version: str = CHUNKER_VERSION


@dataclass(frozen=True)
class _Section:
    path: tuple[str, ...]
    blocks: tuple[Block, ...]


def _sections(title: str | None, blocks: tuple[Block, ...]) -> list[_Section]:
    root: tuple[str, ...] = (title,) if title else ()
    stack: list[tuple[int, str]] = []
    current: list[Block] = []
    current_path = root
    sections: list[_Section] = []

    for block in blocks:
        if block.kind == "heading":
            if any(b.kind != "heading" for b in current):
                sections.append(_Section(current_path, tuple(current)))
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.title))
            current_path = root + tuple(t for _, t in stack)
            current = [block]  # keep the heading line with its content
        else:
            current.append(block)
    if any(b.kind != "heading" for b in current):
        sections.append(_Section(current_path, tuple(current)))
    return sections


def _split_lines(block: Block, limit: int) -> list[Block]:
    """Split a block at line boundaries into pieces of at most ``limit`` tokens."""
    lines = block.text.split("\n")
    pieces: list[Block] = []
    buf: list[str] = []
    start = block.start_line
    for offset, line in enumerate(lines):
        if buf and estimate_tokens("\n".join([*buf, line])) > limit:
            pieces.append(Block(block.kind, "\n".join(buf), start, start + len(buf) - 1))
            buf, start = [], block.start_line + offset
        buf.append(line)
    if buf:
        pieces.append(Block(block.kind, "\n".join(buf), start, start + len(buf) - 1))

    # A single line can still exceed the limit; split it at word boundaries.
    out: list[Block] = []
    for piece in pieces:
        if estimate_tokens(piece.text) <= limit:
            out.append(piece)
            continue
        words, part = piece.text.split(" "), ""
        for word in words:
            candidate = f"{part} {word}" if part else word
            if part and estimate_tokens(candidate) > limit:
                out.append(Block(piece.kind, part, piece.start_line, piece.end_line))
                part = word
            else:
                part = candidate
        if part:
            out.append(Block(piece.kind, part, piece.start_line, piece.end_line))
    return out


def _split_code(block: Block, limit: int) -> list[Block]:
    """Split oversized fenced code at line boundaries, re-fencing every piece."""
    lines = block.text.split("\n")
    opening = lines[0]
    closing = lines[-1] if len(lines) > 1 else opening.strip()[:3]
    body = Block("code", "\n".join(lines[1:-1]), block.start_line + 1, block.end_line - 1)
    fence_cost = estimate_tokens(f"{opening}\n{closing}")
    pieces = _split_lines(body, max(1, limit - fence_cost))
    return [
        Block("code", f"{opening}\n{p.text}\n{closing}", p.start_line, p.end_line) for p in pieces
    ]


def _units(section: _Section, max_tokens: int, max_code_tokens: int) -> list[Block]:
    """Break a section's blocks into units that each fit their limit."""
    units: list[Block] = []
    for block in section.blocks:
        size = estimate_tokens(block.text)
        if block.kind == "code":
            units.extend([block] if size <= max_code_tokens else _split_code(block, max_tokens))
        elif size > max_tokens:
            units.extend(_split_lines(block, max_tokens))
        else:
            units.append(block)
    return units


def _join(blocks: list[Block]) -> str:
    return "\n\n".join(b.text for b in blocks)


def chunk_blocks(
    title: str | None,
    blocks: tuple[Block, ...],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    max_code_tokens: int = DEFAULT_MAX_CODE_TOKENS,
) -> list[Chunk]:
    if max_tokens < 1 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("require max_tokens >= 1 and 0 <= overlap_tokens < max_tokens")
    if max_code_tokens < max_tokens:
        raise ValueError("max_code_tokens must be >= max_tokens")

    groups: list[tuple[tuple[str, ...], list[Block]]] = []
    for section in _sections(title, blocks):
        current: list[Block] = []
        for unit in _units(section, max_tokens, max_code_tokens):
            has_content = any(b.kind != "heading" for b in current)
            if has_content and estimate_tokens(_join([*current, unit])) > max_tokens:
                groups.append((section.path, current))
                current = _overlap(current, overlap_tokens)
                # Drop the overlap again if it would push this unit over the limit.
                if current and estimate_tokens(_join([*current, unit])) > max_tokens:
                    current = []
            current.append(unit)
        if current:
            groups.append((section.path, current))

    chunks: list[Chunk] = []
    for path, group in groups:
        text = _join(group)
        chunks.append(
            Chunk(
                index=len(chunks),
                text=text,
                heading_path=path,
                start_line=group[0].start_line,
                end_line=group[-1].end_line,
                token_estimate=estimate_tokens(text),
                content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return chunks


def _overlap(previous: list[Block], overlap_tokens: int) -> list[Block]:
    """Trailing whole text blocks of ``previous`` that fit in ``overlap_tokens``."""
    carried: list[Block] = []
    for block in reversed(previous):
        if block.kind != "text":
            break
        if estimate_tokens(_join([block, *carried])) > overlap_tokens:
            break
        carried.insert(0, block)
    # Never carry the whole previous chunk, or the next chunk would be a superset.
    return carried if len(carried) < len(previous) else []


def chunk_markdown(source: str, **limits: int) -> list[Chunk]:
    """Parse and chunk one Markdown document."""
    parsed = parse_markdown(source)
    return chunk_blocks(parsed.title, parsed.blocks, **limits)
