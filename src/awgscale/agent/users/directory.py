"""Who the users are: the users service's own list, kept for a minute in a file
(a command run from a shell beside the daemon reads it too) — and, while the
service cannot be asked, the users as last known. An outage must not look like
everybody was removed."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..clients.http import ServiceDown, ServiceRefused
from ..clients.users import UsersClient
from ..ports import Outcome
from ..wire import created_shape, removal_shape
from .models import KnownUser, Made, Removed, RemovedOne

LIST_TTL_SECONDS = 60
NAME_LINE = "#_Name = "
ADDRESS_LINE = "AllowedIPs = "


def _moment(seconds: object) -> datetime | None:
    return datetime.fromtimestamp(seconds, UTC) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None


def user_of(record: dict[str, Any]) -> KnownUser:
    """A user as the cache file holds one: moments as Unix seconds."""
    return KnownUser(name=str(record.get("name") or ""), ip=str(record.get("ip") or ""), expires_at=_moment(record.get("expires_at")), created_at=_moment(record.get("created_at")))


def record_of(user: KnownUser) -> dict[str, Any]:
    record: dict[str, Any] = {"name": user.name, "ip": user.ip, "expires_at": None if user.expires_at is None else int(user.expires_at.timestamp())}
    if user.created_at is not None:
        record["created_at"] = int(user.created_at.timestamp())
    return record


def from_tunnel_file(text: str) -> list[KnownUser]:
    """The names and addresses in the tunnel's own file: what is known of the users when nothing else is."""
    found: list[KnownUser] = []
    name: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(NAME_LINE):
            name = line[len(NAME_LINE):]
        elif line.startswith(ADDRESS_LINE) and name:
            address = line[len(ADDRESS_LINE):].split(",")[0].strip().split("/")[0]
            found.append(KnownUser(name=name, ip=address))
            name = None
    return found


class Directory:
    def __init__(self, client: UsersClient, cache_file: Path, tunnel_file: Path) -> None:
        self.__client = client
        self.__cache_file = cache_file
        self.__tunnel_file = tunnel_file

    def __cached(self, fresh_only: bool) -> list[KnownUser] | None:
        try:
            if fresh_only and time.time() - os.path.getmtime(self.__cache_file) >= LIST_TTL_SECONDS:
                return None
            data = json.loads(self.__cache_file.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, list) or (not fresh_only and not data):
            return None
        return [user_of(record) for record in data if isinstance(record, dict)]

    def __last_known(self) -> list[KnownUser]:
        cached = self.__cached(fresh_only=False)
        if cached:
            return cached
        try:
            return from_tunnel_file(self.__tunnel_file.read_text())
        except OSError:
            return []

    def __remember(self, users: list[KnownUser]) -> None:
        try:
            self.__cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.__cache_file.write_text(json.dumps([record_of(user) for user in users]))
        except OSError:
            pass

    async def list(self, force: bool = False) -> list[KnownUser]:
        if not force:
            cached = await asyncio.to_thread(self.__cached, True)
            if cached is not None:
                return cached
        try:
            views = await self.__client.list()
        except (ServiceDown, ServiceRefused):
            return await asyncio.to_thread(self.__last_known)
        users = [KnownUser(name=view.name, ip=str(view.ip), expires_at=view.expires_at, created_at=view.created_at) for view in views]
        await asyncio.to_thread(self.__remember, users)
        return users


class UsersService:
    """The users service behind its client, for what changes a user or hands out their code."""

    def __init__(self, client: UsersClient) -> None:
        self.__client = client

    async def set_expiry(self, names: list[str], term: str | None) -> Outcome:
        try:
            await self.__client.set_expiry(names, term)
        except ServiceRefused as e:
            return Outcome(False, str(e) or "ошибка")
        except ServiceDown:
            return Outcome(False, "сервис пользователей не отвечает")
        return Outcome(True)

    async def config(self, name: str) -> dict[str, Any] | None:
        try:
            payload = await self.__client.config(name)
        except (ServiceDown, ServiceRefused):
            return None
        return payload.model_dump() if payload else None

    async def add(self, name: str, term: str | None) -> Made | None:
        try:
            created = await self.__client.add(name, term)
        except ServiceRefused as e:
            return Made(ok=False, error=str(e), answer={"ok": False, "error": str(e)})
        except ServiceDown:
            return None
        return Made(ok=True, answer={"ok": True, "results": [created_shape(created)]})

    async def remove(self, names: list[str]) -> Removed | None:
        try:
            removed = await self.__client.remove(names)
        except ServiceRefused as e:
            return Removed(answer={"ok": False, "error": str(e)})
        except ServiceDown:
            return None
        results = tuple(RemovedOne(name=removal.name, status=removal.status.value, error=removal.error) for removal in removed.results)
        return Removed(results=results, answer={"ok": removed.ok, "results": [removal_shape(removal) for removal in removed.results]})
