"""Small TCP helpers for simulating database outages without touching the real database."""

from __future__ import annotations

import asyncio
import contextlib


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


class ToggleProxy:
    """Forwards 127.0.0.1:<port> to a target while up; drops every connection while down."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.up = True
        self._writers: set[asyncio.StreamWriter] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self.up:
            writer.close()
            return
        up_reader, up_writer = await asyncio.open_connection(*self.target)
        self._writers.update({writer, up_writer})
        for task in (
            asyncio.create_task(_pipe(reader, up_writer)),
            asyncio.create_task(_pipe(up_reader, writer)),
        ):
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def set_down(self) -> None:
        """Refuse new connections and cut existing ones, like a database crash."""
        self.up = False
        for writer in self._writers:
            writer.close()
        self._writers.clear()

    def set_up(self) -> None:
        self.up = True

    async def close(self) -> None:
        self.set_down()
        for task in list(self._tasks):
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


class SilentServer:
    """Accepts TCP connections and never replies: a database that hangs."""

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self._writers: list[asyncio.StreamWriter] = []

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        async def _hold(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self._writers.append(writer)

        self._server = await asyncio.start_server(_hold, "127.0.0.1", 0)

    async def close(self) -> None:
        for writer in self._writers:
            writer.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 1)
