"""The store, asked asynchronously: a connection and a transaction per piece of work."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import psycopg
from psycopg.types.json import Jsonb

from awgscale.service.db import transaction
from awgscale.service.errors import Down

from ..errors import StoreDown
from .documents import TABLES, Document, Kind, Mapped, Record, check, rows_of, schema

# a statement or a row lock that does not come is an error, not a wait without end
CONNECTION_OPTIONS = "-c lock_timeout=20000 -c statement_timeout=60000"
READY_KEY = "ready"
DNS_MOVED_KEY = "moved:dns"
ERROR_LENGTH = 200


class StoreTransaction:
    """The settings inside one transaction: whatever is read and written through
    it is stored whole or not at all, also across kinds (a removed user leaves
    the spaces and the groups)."""

    def __init__(self, cursor: psycopg.AsyncCursor[tuple[Any, ...]]) -> None:
        self.__cursor = cursor
        self.__ready = False
        self.__dns_moved = False

    async def ready(self) -> None:
        """Raises StoreDown for a database the settings were never moved into: it is not "no settings"."""
        if not self.__ready:
            await self.__cursor.execute("SELECT to_regclass('meta') IS NOT NULL")
            there = await self.__cursor.fetchone()
            if there and there[0]:
                await self.__cursor.execute("SELECT 1 FROM meta WHERE key = %s", (READY_KEY,))
                self.__ready = await self.__cursor.fetchone() is not None
        if not self.__ready:
            raise StoreDown("база пуста: настройки в неё не перенесены (store-init)")

    async def __dns_ready(self) -> None:
        """The DNS codes joined the store later than the rest: until `store-init`
        has moved them, they are not "none" — they are not readable."""
        if not self.__dns_moved:
            await self.__cursor.execute("SELECT 1 FROM meta WHERE key = %s", (DNS_MOVED_KEY,))
            self.__dns_moved = await self.__cursor.fetchone() is not None
        if not self.__dns_moved:
            raise StoreDown("коды DNS ещё не перенесены в базу (store-init)")

    # ── kinds that are mappings ──────────────────────────────────────────────

    async def suspended(self) -> dict[str, int]:
        """Who is paused, and since when (Unix seconds, as the table holds them)."""
        await self.ready()
        await self.__cursor.execute("SELECT user_name, since FROM suspended")
        return {name: since for name, since in await self.__cursor.fetchall()}

    async def dns_devices(self) -> Mapped:
        await self.ready()
        await self.__dns_ready()
        await self.__cursor.execute("SELECT token, name, created_at FROM dns_devices ORDER BY created_at, name")
        return {token: {"name": name, "created": created} for token, name, created in await self.__cursor.fetchall()}

    # ── ordered kinds ────────────────────────────────────────────────────────

    async def document(self, kind: Kind) -> Document:
        """An ordered kind, whole, as it was stored."""
        await self.ready()
        table = TABLES[kind]
        await self.__cursor.execute(f"SELECT parent_id, value FROM {table.list_table} ORDER BY parent_id, pos")
        entries: dict[str, list[str]] = {}
        for parent, entry in await self.__cursor.fetchall():
            entries.setdefault(parent, []).append(entry)
        columns = "".join(", " + column.name for column in table.columns)
        await self.__cursor.execute(f"SELECT id, listed, extra{columns} FROM {table.name} ORDER BY pos")
        records: Document = []
        for row in await self.__cursor.fetchall():
            record: Record = {"id": row[0]}
            if isinstance(row[2], dict):
                record.update(row[2])
            for column, held in zip(table.columns, row[3:]):
                if held is not None:
                    record[column.field] = held
            if row[1]:
                record[table.list_field] = entries.get(row[0], [])
            records.append(record)
        return records

    async def get(self, kind: Kind) -> Any:
        """Any kind, whole: a mapping for the kinds that are one, a list of records for the rest."""
        if kind is Kind.SUSPENDED:
            return await self.suspended()
        if kind is Kind.DNS:
            return await self.dns_devices()
        return await self.document(kind)

    async def put(self, kind: Kind, value: object) -> None:
        """Replaces a kind whole. Raises StoreRefused for what the store does not take."""
        await self.ready()
        if kind is Kind.DNS:
            await self.__dns_ready()
        check(kind, value)
        if kind is Kind.DNS:
            assert isinstance(value, dict)
            await self.__cursor.execute("DELETE FROM dns_devices")
            if value:
                devices = [(token, device["name"], device["created"]) for token, device in value.items()]
                await self.__cursor.executemany("INSERT INTO dns_devices (token, name, created_at) VALUES (%s, %s, %s)", devices)
            return
        if kind is Kind.SUSPENDED:
            assert isinstance(value, dict)
            await self.__cursor.execute("DELETE FROM suspended")
            if value:
                await self.__cursor.executemany("INSERT INTO suspended (user_name, since) VALUES (%s, %s)", list(value.items()))
            return
        table = TABLES[kind]
        rows = rows_of(kind, value)
        await self.__cursor.execute(f"DELETE FROM {table.name}")          # its entries go with it (ON DELETE CASCADE)
        columns = "".join(", " + column.name for column in table.columns)
        marks = ", %s" * len(table.columns)
        if rows:
            await self.__cursor.executemany(
                f"INSERT INTO {table.name} (id, pos, listed, extra{columns}) VALUES (%s, %s, %s, %s{marks})",
                [(row.id, row.position, row.listed, Jsonb(row.extra), *row.typed) for row in rows],
            )
        children = [(row.id, position, entry) for row in rows for position, entry in enumerate(row.entries)]
        if children:
            await self.__cursor.executemany(f"INSERT INTO {table.list_table} (parent_id, pos, value) VALUES (%s, %s, %s)", children)

    # ── single values ────────────────────────────────────────────────────────

    async def meta(self, keys: Iterable[str]) -> dict[str, str]:
        """The values kept under these keys; a key that is not there is not in the answer."""
        await self.ready()
        await self.__cursor.execute("SELECT key, value FROM meta WHERE key = ANY(%s)", (list(keys),))
        return {key: held for key, held in await self.__cursor.fetchall()}

    async def set_meta(self, key: str, value: str) -> None:
        await self.ready()
        await self.__cursor.execute("INSERT INTO meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))

    async def mark(self, key: str, value: str) -> None:
        """A mark that is set once and then left as it is."""
        await self.ready()
        await self.__cursor.execute("INSERT INTO meta (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (key, value))

    async def create_schema(self) -> None:
        for statement in schema():
            await self.__cursor.execute(statement)

    # ── for `store-init` alone: the store before it is ready ─────────────────

    async def marked(self, key: str) -> bool:
        """Is this mark set. Asked without the "ready" check: the tables must exist."""
        await self.__cursor.execute("SELECT 1 FROM meta WHERE key = %s", (key,))
        return await self.__cursor.fetchone() is not None

    def trust(self) -> None:
        """Whoever is moving the settings in writes before the "ready" mark is set, inside the very transaction that sets it."""
        self.__ready = True

    def trust_dns(self) -> None:
        """…and the DNS codes before theirs."""
        self.__dns_moved = True


class Store:
    def __init__(self, dsn: str, password_file: Path) -> None:
        self.__dsn = dsn
        self.__password_file = password_file
        # the transaction this piece of work (this task) has open, if any
        self.__open: ContextVar[StoreTransaction | None] = ContextVar("open_store_transaction", default=None)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[StoreTransaction]:
        """A transaction. Asked for inside another one of the same piece of work, it
        joins that one: a change that touches several kinds (a removed user leaves
        the pauses, the spaces and the groups) is stored whole or not at all, and
        what was written is read back before it is committed."""
        joined = self.__open.get()
        if joined is not None:
            try:
                yield joined
            except psycopg.Error as e:
                raise StoreDown(str(e).strip()[:ERROR_LENGTH]) from None
            return
        try:
            async with transaction(self.__dsn, self.__password_file, CONNECTION_OPTIONS) as cursor:
                opened = StoreTransaction(cursor)
                token = self.__open.set(opened)
                try:
                    yield opened
                finally:
                    self.__open.reset(token)
        except Down as e:
            raise StoreDown(str(e).removeprefix("database: ")[:ERROR_LENGTH]) from None

    @asynccontextmanager
    async def whole(self) -> AsyncIterator[None]:
        """Everything stored inside the block is stored together (ports.Together)."""
        async with self.transaction():
            yield

    async def ping(self) -> None:
        """Raises StoreDown now rather than halfway: asked before a command that
        changes something else first (a user made or removed by the users service)."""
        async with self.transaction() as tx:
            await tx.ready()

    async def get(self, kind: Kind) -> Any:
        async with self.transaction() as tx:
            return await tx.get(kind)

    async def put(self, kind: Kind, value: object) -> None:
        async with self.transaction() as tx:
            await tx.put(kind, value)


def same(first: object, second: object) -> bool:
    """Do two documents say the same, whatever the order of their keys."""
    return json.dumps(first, sort_keys=True, ensure_ascii=False) == json.dumps(second, sort_keys=True, ensure_ascii=False)
