"""The agent on its unix socket."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from .dispatcher import Dispatcher
from .protocol import MAX_LINE_BYTES, Refusal, Reply, SocketRequest

log = logging.getLogger("awgscale.agent")

SOCKET_DIR_MODE = 0o750
SOCKET_UMASK = 0o117            # the socket: read and write for its owner and group only
ERROR_LENGTH = 300


class Daemon:
    def __init__(self, dispatcher: Dispatcher, socket_path: Path, backlog: int) -> None:
        self.__dispatcher = dispatcher
        self.__socket_path = socket_path
        self.__backlog = backlog
        self.__server: asyncio.Server | None = None

    async def answer(self, line: bytes) -> Reply:
        """One line of the protocol → the answer. Never raises."""
        try:
            data = json.loads(line or b"{}")
            if not isinstance(data, dict):
                return Refusal(error=f"'{type(data).__name__}' object has no attribute 'get'").reply()
            request = SocketRequest.model_validate(data)
            command = request.command()
            if command is None:
                return Refusal(error="bad request").reply()
            return await self.__dispatcher.run(command, request.caller(), request.secret_for(command))
        except Exception as e:
            return Refusal(error=str(e)[:ERROR_LENGTH]).reply()

    async def __handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await reader.readline()
            except ValueError:                   # a line longer than the protocol allows
                line = b"[]"
            reply = await self.answer(line)
            writer.write(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except OSError:
            pass                                 # whoever asked has gone
        finally:
            writer.close()

    async def start(self) -> None:
        self.__socket_path.parent.mkdir(mode=SOCKET_DIR_MODE, parents=True, exist_ok=True)
        self.__socket_path.unlink(missing_ok=True)
        previous = os.umask(SOCKET_UMASK)
        try:
            self.__server = await asyncio.start_unix_server(self.__handle, path=str(self.__socket_path), backlog=self.__backlog, limit=MAX_LINE_BYTES)
        finally:
            os.umask(previous)
        log.info("agent listening on %s", self.__socket_path)

    async def stop(self, wait_seconds: float) -> None:
        """No new connections; the commands that run are waited for."""
        if self.__server is not None:
            self.__server.close()
        left = await self.__dispatcher.stop(wait_seconds)
        log.info("agent stopped%s", f", {left} command(s) cut" if left else "")
