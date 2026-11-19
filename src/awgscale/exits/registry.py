"""What the service keeps between restarts: the servers it knows and the last job.
Two values beside the settings (the database's table `meta`); the tests keep them in a file."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

from awgscale.service.db import transaction

from .models import Job, Node

NODES_KEY = "exitnodes"
JOB_KEY = "exitjob"

Document = dict[str, Any]


class KeyValue(Protocol):
    async def get(self, key: str) -> Document: ...

    async def put(self, key: str, value: Document) -> None: ...


class FileKeyValue:
    def __init__(self, path: Path) -> None:
        self.__path = path

    def __all(self) -> Document:
        try:
            data = json.loads(self.__path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    async def get(self, key: str) -> Document:
        return self.__all().get(key) or {}

    async def put(self, key: str, value: Document) -> None:
        data = self.__all() | {key: value}
        temporary = self.__path.with_name(self.__path.name + ".new")
        temporary.write_text(json.dumps(data))
        os.replace(temporary, self.__path)


class DatabaseKeyValue:
    def __init__(self, dsn: str, password_file: Path) -> None:
        self.__dsn = dsn
        self.__password_file = password_file

    async def get(self, key: str) -> Document:
        async with transaction(self.__dsn, self.__password_file) as cursor:
            await cursor.execute("SELECT value FROM meta WHERE key = %s", (key,))
            row = await cursor.fetchone()
        try:
            value = json.loads(str(row[0])) if row else {}
        except ValueError:
            value = {}
        return value if isinstance(value, dict) else {}

    async def put(self, key: str, value: Document) -> None:
        async with transaction(self.__dsn, self.__password_file) as cursor:
            await cursor.execute(
                "INSERT INTO meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
            )


class Registry:
    """The servers behind the exits, by exit number, and the job running or last run."""

    def __init__(self, store: KeyValue) -> None:
        self.__store = store
        self.__changes = asyncio.Lock()        # read, change, write: one at a time

    async def nodes(self) -> dict[int, Node]:
        return {int(index): Node.model_validate(node) for index, node in (await self.__store.get(NODES_KEY)).items()}

    async def node(self, index: int) -> Node | None:
        return (await self.nodes()).get(index)

    async def set_node(self, index: int, **fields: object) -> Node:
        """Writes these fields of a server down, keeping the rest of what is known about it."""
        async with self.__changes:
            raw = await self.__store.get(NODES_KEY)
            node = Node.model_validate((raw.get(str(index)) or {}) | fields)
            raw[str(index)] = node.wire()
            await self.__store.put(NODES_KEY, raw)
            return node

    async def drop_node(self, index: int) -> None:
        async with self.__changes:
            raw = await self.__store.get(NODES_KEY)
            if raw.pop(str(index), None) is not None:
                await self.__store.put(NODES_KEY, raw)

    async def save_job(self, job: Job) -> None:
        async with self.__changes:
            await self.__store.put(JOB_KEY, job.wire())

    async def last_job(self) -> Job | None:
        raw = await self.__store.get(JOB_KEY)
        return Job.model_validate(raw) if raw else None
