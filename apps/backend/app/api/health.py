"""Liveness and readiness endpoints.

Liveness never touches the database, so it keeps answering during a database
outage. Readiness checks connectivity and the pgvector extension under a hard
deadline and returns a generic 503 body; details go to the log only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings

router = APIRouter(prefix="/health", tags=["health"])
log = logging.getLogger(__name__)

CheckState = Literal["ok", "unavailable", "missing", "skipped"]


class LiveResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, CheckState]


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse(status="ok")


async def _check(engine: AsyncEngine) -> dict[str, CheckState]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        has_vector = (
            await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        ).scalar() is not None
    return {"database": "ok", "vector_extension": "ok" if has_vector else "missing"}


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse}},
)
async def ready(request: Request) -> JSONResponse:
    engine: AsyncEngine = request.app.state.engine
    settings: Settings = request.app.state.settings
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            checks = await _check(engine)
    except Exception as exc:  # any failure means "not ready"; never leak the details
        log.warning("readiness check failed", extra={"error_type": type(exc).__name__})
        checks = {"database": "unavailable", "vector_extension": "skipped"}

    ok = all(state == "ok" for state in checks.values())
    body = ReadyResponse(status="ready" if ok else "unavailable", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)
