"""The stack's database, asked asynchronously: one connection per piece of work, one transaction on it."""
from __future__ import annotations

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
