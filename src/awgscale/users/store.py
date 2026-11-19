"""The users in the database. Nothing else writes these tables."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from ipaddress import IPv4Address
from typing import AsyncIterator, Sequence

import psycopg
from pydantic import SecretStr

from awgscale.service.db import transaction
from awgscale.service.errors import Down

from .models import User
from .settings import UsersSettings

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key text PRIMARY KEY, value text NOT NULL)",
    """CREATE TABLE IF NOT EXISTS users (
        seq bigserial NOT NULL,
        name text PRIMARY KEY,
        ip text NOT NULL UNIQUE,
        public_key text NOT NULL UNIQUE,
        private_key text NOT NULL,
        preshared_key text NOT NULL,
        expires_at timestamptz,
        created_at timestamptz NOT NULL)""",
)
# The users' moments were once kept as Unix seconds: such a table is brought to real timestamps, once.
TIME_COLUMNS = ("expires_at", "created_at")
UNIX_SECONDS_TYPE = "bigint"
COLUMNS = "name, ip, public_key, private_key, preshared_key, expires_at, created_at"
READY_KEY = "users_ready"

Row = tuple[str, str, str, str, str, datetime | None, datetime]


def _user(row: Row) -> User:
    name, ip, public_key, private_key, preshared_key, expires_at, created_at = row
    return User(
        name=name,
        ip=IPv4Address(ip),
        public_key=public_key,
        private_key=SecretStr(private_key),
        preshared_key=SecretStr(preshared_key),
        expires_at=expires_at,
        created_at=created_at,
    )


def _row(user: User) -> Row:
    return (
        user.name,
        str(user.ip),
        user.public_key,
        user.private_key.get_secret_value(),
        user.preshared_key.get_secret_value(),
        user.expires_at,
        user.created_at,
    )


class UsersTransaction:
    """The users inside one transaction."""

    def __init__(self, cursor: psycopg.AsyncCursor[tuple[object, ...]]) -> None:
        self.__cursor = cursor

    async def is_ready(self) -> bool:
        """Were the users ever moved into the database. Until then nothing is answered from it."""
        await self.__cursor.execute("SELECT to_regclass('meta') IS NOT NULL AND to_regclass('users') IS NOT NULL")
        tables = await self.__cursor.fetchone()
        if not tables or not tables[0]:
            return False
        await self.__cursor.execute("SELECT 1 FROM meta WHERE key = %s", (READY_KEY,))
        return await self.__cursor.fetchone() is not None

    async def all(self) -> list[User]:
        """Every user, in the order they were made."""
        if not await self.is_ready():
            raise Down("the users were not moved into the database yet (python -m awgscale.users import)")
        await self.__cursor.execute(f"SELECT {COLUMNS} FROM users ORDER BY seq")
        return [_user(row) for row in await self.__cursor.fetchall()]      # type: ignore[arg-type]

    async def __ready(self) -> None:
        if not await self.is_ready():
            raise Down("the users were not moved into the database yet (python -m awgscale.users import)")

    async def get(self, name: str) -> User | None:
        """One user by name: the others are not read."""
        await self.__ready()
        await self.__cursor.execute(f"SELECT {COLUMNS} FROM users WHERE name = %s", (name,))
        row = await self.__cursor.fetchone()
        return None if row is None else _user(row)      # type: ignore[arg-type]

    async def count(self) -> int:
        await self.__ready()
        await self.__cursor.execute("SELECT count(*) FROM users")
        row = await self.__cursor.fetchone()
        return int(row[0]) if row else 0               # type: ignore[call-overload]

    async def addresses(self) -> set[IPv4Address]:
        """The addresses taken: only that column."""
        await self.__ready()
        await self.__cursor.execute("SELECT ip FROM users")
        return {IPv4Address(str(row[0])) for row in await self.__cursor.fetchall()}

    async def known(self, names: Sequence[str]) -> set[str]:
        """Which of these names are users."""
        await self.__ready()
        await self.__cursor.execute("SELECT name FROM users WHERE name = ANY(%s)", (list(names),))
        return {str(row[0]) for row in await self.__cursor.fetchall()}

    async def insert(self, user: User) -> None:
        await self.__cursor.execute(f"INSERT INTO users ({COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s)", _row(user))

    async def delete(self, name: str) -> None:
        await self.__cursor.execute("DELETE FROM users WHERE name = %s", (name,))

    async def delete_exactly(self, user: User) -> None:
        """Takes back a user made a moment ago — and nobody else who may have the name by now."""
        await self.__cursor.execute("DELETE FROM users WHERE name = %s AND public_key = %s", (user.name, user.public_key))

    async def set_expiry(self, names: Sequence[str], expires_at: datetime | None) -> None:
        await self.__cursor.execute("UPDATE users SET expires_at = %s WHERE name = ANY(%s)", (expires_at, list(names)))

    async def create_schema(self) -> None:
        for statement in SCHEMA:
            await self.__cursor.execute(statement)

    async def mark_ready(self) -> None:
        await self.__cursor.execute("INSERT INTO meta (key, value) VALUES (%s, now()::text)", (READY_KEY,))

    async def upgrade(self) -> None:
        """Brings a table that keeps moments as Unix seconds to timestamps. Nothing to do on a table that already has them, or is not there yet."""
        for column in TIME_COLUMNS:
            await self.__cursor.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'users' AND column_name = %s",
                (column,),
            )
            found = await self.__cursor.fetchone()
            if found and found[0] == UNIX_SECONDS_TYPE:
                await self.__cursor.execute(f"ALTER TABLE users ALTER COLUMN {column} TYPE timestamptz USING to_timestamp({column})")


class UserStore:
    def __init__(self, settings: UsersSettings) -> None:
        self.__settings = settings
        self.__upgraded = False

    @asynccontextmanager
    async def __cursor(self) -> AsyncIterator[UsersTransaction]:
        async with transaction(self.__settings.dsn, self.__settings.db_password_file) as cursor:
            yield UsersTransaction(cursor)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[UsersTransaction]:
        """One transaction on the users; the first one of a process brings an older table up to date before it."""
        if not self.__upgraded:
            async with self.__cursor() as tx:
                await tx.upgrade()
            self.__upgraded = True
        async with self.__cursor() as tx:
            yield tx

    async def all(self) -> list[User]:
        async with self.transaction() as tx:
            return await tx.all()

    async def get(self, name: str) -> User | None:
        async with self.transaction() as tx:
            return await tx.get(name)
