"""Reading: the newest entries as Loki holds them."""
from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any, Sequence

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..clients.tls import tls_context
from .models import StoredEntry

DEFAULT_LIMIT = 300
AHEAD = timedelta(seconds=60)          # the window ends a little in the future: clocks differ
NANOSECONDS = 1e9
TIMEOUT_SECONDS = 20.0


class Stream(BaseModel):
    values: list[tuple[str, str]] = Field(default_factory=list, description="[time in nanoseconds, the line] pairs")


class Streams(BaseModel):
    result: list[Stream] = Field(default_factory=list, description="Every stream that answered")


class QueryAnswer(BaseModel):
    data: Streams = Field(description="What the query found")


def entries_of(lines: Sequence[str]) -> list[dict[str, Any]]:
    """Lines as they were written → the entries the panel shows; what is not an entry (a cut line, a stray value) is left out."""
    entries: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        try:
            entries.append(StoredEntry.model_validate(item).wire())
        except ValidationError:
            entries.append(item)
    return entries


class JournalReader:
    def __init__(self, loki_url: str, identifier: str, days: int, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.__loki_url = loki_url.rstrip("/")
        self.__identifier = identifier
        self.__days = days
        self.__transport = transport

    async def lines(self, limit: int = DEFAULT_LIMIT) -> list[str]:
        """The newest lines, newest first. Several streams can answer (labels Loki
        adds on its own), so they are merged by time here. Raises when Loki cannot be asked."""
        now = time.time()
        query = {
            "query": '{job="%s"}' % self.__identifier,
            "limit": limit,
            "direction": "backward",
            "start": int((now - timedelta(days=self.__days).total_seconds()) * NANOSECONDS),
            "end": int((now + AHEAD.total_seconds()) * NANOSECONDS),
        }
        async with httpx.AsyncClient(base_url=self.__loki_url, transport=self.__transport, timeout=TIMEOUT_SECONDS, verify=tls_context()) as client:
            response = await client.get("/loki/api/v1/query_range", params=query)
        response.raise_for_status()
        answer = QueryAnswer.model_validate(response.json())
        rows = [row for stream in answer.data.result for row in stream.values]
        rows.sort(key=lambda row: int(row[0]), reverse=True)
        return [line for _, line in rows[:limit]]
