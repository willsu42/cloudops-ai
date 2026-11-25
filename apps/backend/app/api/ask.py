"""POST /api/ask: a grounded answer with citations, or an explicit abstention.

Synchronous: retrieval and drafting happen within the request. The question
text is never logged.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.rag.answer import Answer, answer_question
from app.rag.retrieval import IndexMismatch, InvalidQuery

router = APIRouter(prefix="/api", tags=["ask"])
log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 1000


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    services: list[str] | None = Field(default=None, max_length=10)


class CitationOut(BaseModel):
    chunk_id: uuid.UUID
    label: str
    source_key: str
    title: str
    heading_path: list[str]
    start_line: int
    end_line: int


class ClaimOut(BaseModel):
    text: str
    citations: list[CitationOut]


class RetrievedOut(BaseModel):
    chunk_id: uuid.UUID
    label: str
    source_key: str
    title: str
    service: str | None
    heading_path: list[str]
    start_line: int
    end_line: int
    score: float


class AskResponse(BaseModel):
    abstained: bool
    summary: str
    claims: list[ClaimOut]
    limitations: list[str]
    retrieved: list[RetrievedOut]
    llm_model: str | None
    embedding_model: str


def _to_response(answer: Answer) -> AskResponse:
    return AskResponse(
        abstained=answer.abstained,
        summary=answer.summary,
        claims=[
            ClaimOut(
                text=c.text,
                citations=[
                    CitationOut(
                        chunk_id=x.chunk_id,
                        label=x.label,
                        source_key=x.source_key,
                        title=x.title,
                        heading_path=list(x.heading_path),
                        start_line=x.start_line,
                        end_line=x.end_line,
                    )
                    for x in c.citations
                ],
            )
            for c in answer.claims
        ],
        limitations=list(answer.limitations),
        retrieved=[
            RetrievedOut(
                chunk_id=r.chunk_id,
                label=answer.labels[r.chunk_id],
                source_key=r.source_key,
                title=r.title,
                service=r.service,
                heading_path=list(r.heading_path),
                start_line=r.start_line,
                end_line=r.end_line,
                score=r.score,
            )
            for r in answer.retrieved
        ],
        llm_model=answer.llm_model,
        embedding_model=answer.embedding_model,
    )


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={400: {}, 502: {}, 503: {}},
)
async def ask(body: AskRequest, request: Request) -> AskResponse:
    state = request.app.state
    try:
        answer = await answer_question(
            state.engine,
            state.settings,
            state.embedding_provider,
            state.llm_provider,
            body.question,
            services=body.services,
        )
    except InvalidQuery as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except IndexMismatch as exc:
        log.warning("ask failed: index mismatch", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="index not ready") from None
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        log.warning("ask failed: database", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="database unavailable") from None
    except Exception as exc:  # provider failures; never leak their details
        log.warning("ask failed: provider", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=502, detail="answer generation failed") from None
    return _to_response(answer)
