"""The stack's database, asked asynchronously: one transaction per piece of work, on a connection of its own or one
kept open for the next piece of work (Database)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import psycopg

from .errors import Down

ERROR_LENGTH = 160


def _down(e: Exception) -> Down:
    return Down(f"database: {str(e).strip()[:ERROR_LENGTH]}")


@asynccontextmanager
async def transaction(dsn: str, password_file: Path, options: str | None = None) -> AsyncIterator[psycopg.AsyncCursor[tuple[object, ...]]]:
    """A cursor inside one transaction: committed when the block ends, rolled
    back when it raises. A database that cannot be reached, or refuses, is Down.
    `options` are the connection's own settings (say, how long a lock is waited for)."""
    try:
        connection = await psycopg.AsyncConnection.connect(dsn, password=password_file.read_text().strip(), options=options)
    except (OSError, psycopg.Error) as e:
        raise _down(e) from None
    try:
        async with connection.cursor() as cursor:
            yield cursor
        await connection.commit()
    except psycopg.Error as e:
        raise _down(e) from None
    finally:
        await connection.close()



class Database:
    """The connections a long-lived service keeps open to the database: a new one costs a login (tens of
    milliseconds), a kept one a round trip. A piece of work takes an idle one (asked first whether it still answers:
    a database restarted under it costs a fresh login, not an error) or logs in anew, exactly as a connection of its
    own would; when the work is done the connection is kept for the next one, up to `size` of them. No background
    task: what is kept belongs to the event loop that made it, another loop starts with none."""

    def __init__(self, dsn: str, password_file: Path, options: str | None, size: int) -> None:
        self.__dsn = dsn
        self.__password_file = password_file
        self.__options = options
        self.__size = size
        self.__idle: list[psycopg.AsyncConnection[tuple[object, ...]]] = []
        self.__loop: asyncio.AbstractEventLoop | None = None

    async def __connection(self) -> psycopg.AsyncConnection[tuple[object, ...]]:
        loop = asyncio.get_running_loop()
        if self.__loop is not loop:
            self.__idle = []      # another loop's: left to be collected, never used here
            self.__loop = loop
        while self.__idle:
            kept = self.__idle.pop()
            try:
                await kept.execute("")
                return kept
            except psycopg.Error:
                await kept.close()
        try:
            return await psycopg.AsyncConnection.connect(self.__dsn, password=self.__password_file.read_text().strip(), options=self.__options)
        except (OSError, psycopg.Error) as e:
            raise _down(e) from None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[psycopg.AsyncCursor[tuple[object, ...]]]:
        """A cursor inside one transaction: committed when the block ends, rolled back when it raises. A database
        that cannot be reached, or refuses, is Down."""
        connection = await self.__connection()
        kept = False
        try:
            async with connection.cursor() as cursor:
                yield cursor
            await connection.commit()
            kept = True
        except psycopg.Error as e:
            raise _down(e) from None
        finally:
            if not kept and not connection.closed and not connection.broken:
                try:
                    await connection.rollback()
                    kept = True
                except psycopg.Error:
                    pass
            if kept and len(self.__idle) < self.__size and asyncio.get_running_loop() is self.__loop:
                self.__idle.append(connection)
            else:
                await connection.close()

    async def close(self) -> None:
        """The kept connections closed (at the end of the process)."""
        idle = self.__idle
        self.__idle = []
        if self.__loop is asyncio.get_running_loop():
            for connection in idle:
                await connection.close()
