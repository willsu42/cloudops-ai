"""Reciprocal Rank Fusion (pure function)."""

from __future__ import annotations

import uuid

import pytest

from app.rag.retrieval import RRF_K, RetrievedChunk, fuse


def _chunk(n: int, *, vector: float | None = None, lexical: float | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(int=n),
        source_key=f"doc-{n}",
        title="t",
        service=None,
        heading_path=("t",),
        start_line=1,
        end_line=1,
        text=f"chunk {n}",
        score=0.0,
        vector_distance=vector,
        lexical_score=lexical,
    )


def test_chunk_in_both_channels_outranks_single_channel_leaders() -> None:
    vector = [_chunk(1, vector=0.1), _chunk(2, vector=0.0)]
    lexical = [_chunk(3, lexical=0.9), _chunk(2, lexical=0.5)]

    fused = fuse([vector, lexical], top_k=3)

    assert [c.chunk_id.int for c in fused] == [2, 1, 3]
    assert fused[0].score == pytest.approx(2 / (RRF_K + 2))
    # Channel scores are kept, including a vector distance of exactly 0.
    assert fused[0].vector_distance == 0.0
    assert fused[0].lexical_score == 0.5


def test_ties_break_by_chunk_id_and_top_k_cuts() -> None:
    fused = fuse([[_chunk(9)], [_chunk(4)]], top_k=1)
    assert [c.chunk_id.int for c in fused] == [4]


def test_empty_channels_fuse_to_nothing() -> None:
    assert fuse([[], []], top_k=5) == []
