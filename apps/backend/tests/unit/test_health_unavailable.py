"""Readiness under database outages, using fake endpoints (no real database needed)."""

from __future__ import annotations

import asyncio
import socket
import time

from tests.conftest import ClientFactory, make_settings
from tests.support.tcp import SilentServer

TIMEOUT = 1.0
# Generous slack for slow CI machines; the point is "bounded", not "instant".
BOUND = TIMEOUT + 1.5


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _url(port: int) -> str:
    return f"postgresql+asyncpg://u:p@127.0.0.1:{port}/app_test"


async def test_refused_connection_returns_safe_503(make_client: ClientFactory) -> None:
    client = await make_client(
        make_settings(_url(_closed_port()), readiness_timeout_seconds=TIMEOUT)
    )
    start = time.perf_counter()
    response = await client.get("/health/ready")
    assert time.perf_counter() - start < BOUND
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "unavailable", "vector_extension": "skipped"},
    }
    assert "127.0.0.1" not in response.text  # no connection details leak


async def test_hanging_database_is_bounded_and_liveness_still_answers(
    make_client: ClientFactory,
) -> None:
    server = SilentServer()
    await server.start()
    try:
        client = await make_client(
            make_settings(
                _url(server.port),
                readiness_timeout_seconds=TIMEOUT,
                db_connect_timeout_seconds=TIMEOUT,
            )
        )
        start = time.perf_counter()
        ready_task = asyncio.create_task(client.get("/health/ready"))
        await asyncio.sleep(0.1)  # readiness is now blocked on the silent server
        live = await client.get("/health/live")
        live_elapsed = time.perf_counter() - start
        ready = await ready_task
        ready_elapsed = time.perf_counter() - start
    finally:
        await server.close()

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert live_elapsed < TIMEOUT, "liveness waited on the database"
    assert ready.status_code == 503
    assert ready_elapsed < BOUND


async def test_request_id_is_echoed_or_generated(make_client: ClientFactory) -> None:
    client = await make_client(make_settings(_url(_closed_port())))
    echoed = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["X-Request-ID"] == "abc-123"
    generated = await client.get("/health/live", headers={"X-Request-ID": "bad id\n"})
    assert generated.headers["X-Request-ID"] not in ("", "bad id\n")
