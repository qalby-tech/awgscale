"""The operations on the users: make, remove, change the term, hand out a device's config — and what the service does by itself."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import SecretStr

from awgscale.core.clock import utc_now
from awgscale.service.errors import Down, Refused

from .conf import TunnelFile
from .device import DeviceConfigs
from .kernel import Kernel
from .keys import new_preshared, new_private, public_of
from .models import AddRequest, Created, DevicePayload, ExpiryRequest, Health, Removal, RemovalReason, Status, Term, User, UserView
from .settings import UsersSettings
from .store import UserStore

log = logging.getLogger("awgscale.users")

Clock = Callable[[], datetime]
ERROR_LENGTH = 160
SHOWN_MISSING = 5
TERM_JOB_PREFIX = "term:"
INTERFACE_SETTLE_SECONDS = 1.0            # whoever brings the tunnel up loads its file first: its peers are compared after that


class UsersService:
    def __init__(self, settings: UsersSettings, store: UserStore, kernel: Kernel, file: TunnelFile, devices: DeviceConfigs, scheduler: AsyncIOScheduler, clock: Clock = utc_now) -> None:
        self.__settings = settings
        self.__store = store
        self.__kernel = kernel
        self.__file = file
        self.__devices = devices
        self.__scheduler = scheduler
        self.__clock = clock
        self.__changes = asyncio.Lock()        # one change at a time: address allocation, the kernel, the compiled file

    def __until(self, term: Term | None) -> datetime | None:
        return None if term is None else self.__clock() + term.duration

    # ── reading ──────────────────────────────────────────────────────────────

    async def list(self) -> list[UserView]:
        return [UserView.of(user) for user in await self.__store.all()]

    async def health(self) -> Health:
        users = await self.__store.all()
        peers = await self.__kernel.peers()
        return Health(users=len(users), peers=len(peers))

    async def config(self, name: str) -> DevicePayload:
        user = await self.__store.get(name)
        if user is None:
            raise Refused("нет такого пользователя")
        block = await asyncio.to_thread(self.__file.interface)
        return await asyncio.to_thread(self.__devices.payload, user, block)

    # ── changing ─────────────────────────────────────────────────────────────

    async def add(self, request: AddRequest) -> Created:
        created_at = self.__clock()
        expires_at = self.__until(request.expires)
        async with self.__changes:
            block = await asyncio.to_thread(self.__file.interface)
            private_key = new_private()
            async with self.__store.transaction() as tx:
                if await tx.get(request.name) is not None:
                    raise Refused("пользователь с таким именем уже есть")
                if await tx.count() >= self.__settings.max_users:
                    raise Refused(f"не больше {self.__settings.max_users} пользователей")
                address = block.free_address(await tx.addresses())
                if address is None:
                    raise Refused("свободных адресов нет")
                user = User(
                    name=request.name,
                    ip=address,
                    public_key=public_of(private_key),
                    private_key=SecretStr(private_key),
                    preshared_key=SecretStr(new_preshared()),
                    expires_at=expires_at,
                    created_at=created_at,
                )
                await tx.insert(user)
            try:
                await self.__kernel.add_peer(user)
            except Down:
                async with self.__store.transaction() as tx:
                    await tx.delete_exactly(user)
                raise
            warning: str | None = None
            try:
                await asyncio.to_thread(self.__file.append, user)      # its block at the end; the others are not written again
            except (Down, OSError, ValueError) as e:
                warning = str(e)
                log.error("ERROR the boot file was not written after add: %s", e)
        log.info("added %s %s until %s", user.name, user.ip, expires_at)
        self.__plan_term(user.name, expires_at)
        return Created(name=user.name, ip=user.ip, expires_at=expires_at, warning=warning)

    async def remove(self, names: Sequence[str], reason: RemovalReason = RemovalReason.REMOVED) -> list[Removal]:
        """Each name is answered for separately: removed, not found, or failed."""
        async with self.__changes:
            return [await self.__remove_one(name, reason) for name in names]

    async def __remove_one(self, name: str, reason: RemovalReason) -> Removal:
        try:
            user = await self.__store.get(name)
            if user is None:
                return Removal(name=name, status=Status.NOT_FOUND)
            # first the kernel: a row without a peer is put back by the pass, a peer without a row is not known to anybody
            await self.__kernel.remove_peer(user.public_key)
            async with self.__store.transaction() as tx:
                await tx.delete(name)
            try:
                await asyncio.to_thread(self.__file.cut, user.public_key)      # its block out; the others stay as they are
            except (Down, OSError, ValueError) as e:
                log.error("ERROR the boot file was not written after remove: %s", e)
        except Down as e:
            return Removal(name=name, status=Status.FAILED, error=str(e)[:ERROR_LENGTH])
        log.info("%s %s %s", reason.value, name, user.ip)
        self.__plan_term(name, None)
        return Removal(name=name, status=Status.REMOVED)

    async def set_expiry(self, request: ExpiryRequest) -> datetime | None:
        """A new term from now for all the named users, or for nobody. → when it ends."""
        expires_at = self.__until(request.expires)
        async with self.__changes, self.__store.transaction() as tx:
            known = await tx.known(request.names)
            missing = [name for name in request.names if name not in known]
            if missing:
                raise Refused("нет такого пользователя: " + ", ".join(missing[:SHOWN_MISSING]))
            await tx.set_expiry(request.names, expires_at)
        log.info("term %s until %s", ",".join(request.names), expires_at)
        for name in request.names:
            self.__plan_term(name, expires_at)
        return expires_at

    # ── terms: one scheduled job per user who has one ────────────────────────

    def __plan_term(self, name: str, expires_at: datetime | None) -> None:
        """The job that ends this user's access at that moment; no term, no job. A moment already past fires at once."""
        job_id = TERM_JOB_PREFIX + name
        if expires_at is None:
            if self.__scheduler.get_job(job_id):
                self.__scheduler.remove_job(job_id)
            return
        self.__scheduler.add_job(self.expire, "date", run_date=expires_at, args=[name], id=job_id, replace_existing=True, misfire_grace_time=None)

    async def expire(self, name: str) -> None:
        """What a term's job does: removes the user — if the term is really over (it may have been changed meanwhile)."""
        try:
            user = await self.__store.get(name)
            if user is None or user.expires_at is None or user.expires_at > self.__clock():
                return
            removal = (await self.remove([name], RemovalReason.TERM_OVER))[0]
            if removal.status is not Status.FAILED:
                return
            log.info("term of %s not ended: %s", name, removal.error)
        except Down as e:
            log.info("term of %s not ended: %s", name, e)
        self.__plan_term(name, self.__clock() + timedelta(seconds=self.__settings.retry_seconds))     # the database or the kernel did not answer: once more, soon

    async def plan_terms(self) -> None:
        """At the start: a job for every term there is. Waits for the database for as long as it takes."""
        while True:
            try:
                users = await self.__store.all()
            except Down as e:
                log.info("terms not planned yet: %s", e)
                await asyncio.sleep(self.__settings.retry_seconds)
                continue
            for user in users:
                self.__plan_term(user.name, user.expires_at)
            return

    # ── the kernel's peers ───────────────────────────────────────────────────

    async def restore_lost_peers(self) -> None:
        """Puts back the peers the kernel does not have. Never removes a peer it does not know."""
        users = await self.__store.all()
        async with self.__changes:
            present = await self.__kernel.peers()
            for user in users:
                if user.public_key not in present:
                    await self.__kernel.add_peer(user)
                    log.info("put back into the kernel: %s %s", user.name, user.ip)

    async def watch_kernel(self) -> None:
        """Completes the kernel's peers when the service starts and every time the
        tunnel's interface comes up (the kernel says so; nothing is polled). It never dies."""
        while True:
            try:
                await self.restore_lost_peers()
                async for _ in self.__kernel.came_up():
                    await asyncio.sleep(INTERFACE_SETTLE_SECONDS)
                    await self.restore_lost_peers()
            except Down as e:
                log.info("kernel not looked at: %s", e)
            except Exception as e:
                log.error("watching the kernel failed: %s", repr(e)[:200])
            await asyncio.sleep(self.__settings.retry_seconds)
