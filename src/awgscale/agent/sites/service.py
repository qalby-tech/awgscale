"""The commands about the sites that go abroad."""
from __future__ import annotations

import asyncio
import logging
import ipaddress
import socket
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from awgscale.core.clock import utc_now
from awgscale.core.net import HTTPS_PORT

from ..commands import Check, ModeSet, Probe, RouteMode, SitesGet, SitesRefresh, SitesSet
from ..errors import Refused, StoreDown
from ..labels import new_id
from ..locks import file_lock
from ..payloads import decode
from ..ports import BlockFollower, BlockGroups, Blocker, ListSource, ModeStore, Prober, ProfilesNudge, RulesView, SitesStore
from ..store.documents import Record
from ..rules.compile import routing_covered, shown_covered
from ..rules.scoped import ScopedName, routed_groups, scoped_names, view_groups
from ..rules.store import Part
from .blocked import blocked_by
from .entries import DOMAIN_RE, covers, is_network, resolver_names
from .groups import NewId, checked_groups, direct_entries, flat_entries, typed_entries, version_of_sites
from .lists import ListFailed
from .models import Blocked, Checked, Probed, Route, SiteGroup, SitesShown, Via, WaySeen
from .probe import PROBE_PATH_RE, ways_of
from .routing import UPSTREAM_BYTES, NotApplied, Routing, route_client_fits, text_or_empty, upstreams, upstreams_fit

Clock = Callable[[], datetime]
SHOWN_STORED_ERROR = 160

log = logging.getLogger("awgscale.agent")
TRUSTED_WITHOUT_LOOKING = 20        # a list this long is not believed to have halved overnight
DEFAULT_PAGE = "/"


def typed_scoped(scoped: Sequence[ScopedName]) -> list[str]:
    """The names the rules for some groups decide, each once: resolved right away like the entries typed for everybody."""
    return sorted({pair.name for pair in scoped})


class NotSaved(Exception):
    """A change of the groups that was not stored; the text is for the admin."""


class SitesService:
    def __init__(
        self,
        store: SitesStore,
        modes: ModeStore,
        routing: Routing,
        lists: ListSource,
        blocker: Blocker,
        block_groups: BlockGroups,
        prober: Prober,
        cascade_conf: Path,
        lock_file: Path,
        upstream_bytes: int = UPSTREAM_BYTES,
        make_id: NewId = new_id,
        clock: Clock = utc_now,
        rules: RulesView | None = None,
        block_rules: BlockFollower | None = None,
        profiles: ProfilesNudge | None = None,
    ) -> None:
        self.__profiles = profiles              # the clients of the groups sent abroad or directly ask with the upstreams of their own list
        self.__rules = rules                    # the whole list: what a group covers depends on the rules above it
        self.__block_rules = block_rules
        self.__store = store
        self.__modes = modes
        self.__routing = routing
        self.__lists = lists
        self.__blocker = blocker
        self.__block_groups = block_groups
        self.__prober = prober
        self.__cascade_conf = cascade_conf
        self.__lock_file = lock_file            # one writer of the sites at a time: the panel and the nightly refresh
        self.__upstream_bytes = upstream_bytes
        self.__make_id = make_id
        self.__clock = clock

    async def __acting(self, groups: Sequence[SiteGroup]) -> list[SiteGroup]:
        """The groups as the routing takes them: an entry a rule above already names is left out (rules/compile.py)."""
        if self.__rules is None:
            return list(groups)
        covered = routing_covered(await self.__rules.preview(Part.SITES, [group.wire() for group in groups]))
        return [group.model_copy(update={"items": tuple(entry for entry in group.items if entry not in covered[group.id])}) if group.id in covered else group for group in groups]

    async def __scoped(self, groups: Sequence[SiteGroup]) -> list[ScopedName]:
        """The names the rules for some groups decide, with these groups of sites in their places (rules/scoped.py)."""
        if self.__rules is None:
            return []
        return scoped_names(await self.__rules.preview(Part.SITES, [group.wire() for group in groups]))

    def __nudge_profiles(self) -> None:
        """The upstreams of a group's own list may have changed: its clients are made again in the background. Never raises."""
        if self.__profiles is None:
            return
        try:
            self.__profiles.nudge()
        except Exception as e:
            log.warning("sites: the groups' clients not nudged: %s", repr(e)[:SHOWN_STORED_ERROR])

    async def __groups_fit(self) -> bool:
        """Does every group a rule sends abroad or directly fit in one client of the blocker (sites/scoped.py)."""
        if self.__rules is None:
            return True
        rules = await self.__rules.read()
        hand = self.__routing.direct_names()
        mode = self.__routing.mode()
        for group in routed_groups(rules):
            seen = view_groups(rules, group)
            servers = upstreams(flat_entries(seen), mode, [*direct_entries(seen), *hand])["upstream_dns"]
            if not route_client_fits(servers):
                return False
        return True

    async def __covered(self, groups: Sequence[SiteGroup]) -> dict[str, dict[str, str]]:
        """What the page marks: the entries a rule above covers, and by which rule."""
        if self.__rules is None:
            return {}
        rules = await self.__rules.preview(Part.SITES, [group.wire() for group in groups])
        ids = {group.id for group in groups}
        return {rule: entries for rule, entries in shown_covered(rules).items() if rule in ids}

    async def __follow_blocking(self) -> None:
        """A group routed above a blocked one unblocks it: the blocker's rules are made again. Never raises."""
        if self.__block_rules is None:
            return
        try:
            await self.__block_rules.follow()
        except Exception as e:
            log.warning("sites: the blocker's rules not made again: %s", repr(e)[:SHOWN_STORED_ERROR])

    async def __shown(self, groups: Sequence[SiteGroup], applied: bool | None = None, already: bool | None = None) -> SitesShown:
        return SitesShown(
            covered=await self.__covered(groups),
            mode=self.__routing.mode(),
            groups=list(groups),
            version=version_of_sites(groups),
            automatic_networks=self.__routing.automatic_networks(),
            applied=applied,
            already=already,
        )

    def __too_many(self, entries: Sequence[str]) -> Refused:
        return Refused(f"слишком много адресов через заграницу ({len(resolver_names(list(entries)))}): блокировщик столько не принимает")

    # ── the groups ───────────────────────────────────────────────────────────

    async def stored_records(self) -> list[Record]:
        """The groups as stored now: what the journal compares a change with."""
        return [group.wire() for group in await self.__store.read()]

    async def show(self, _: SitesGet) -> SitesShown:
        return await self.__shown(await self.__store.read())

    async def __downloaded(self, group: SiteGroup) -> SiteGroup:
        """A list by link with the entries of its file as it is now. Raises ListFailed."""
        assert group.url is not None
        parsed = await self.__lists.read(group.url)
        return group.model_copy(update={"items": tuple(parsed.entries), "skipped": parsed.skipped, "updated": self.__clock().replace(microsecond=0), "error": ""})

    async def __store_and_apply(self, groups: Sequence[SiteGroup], old: Sequence[SiteGroup]) -> bool:
        """Stores the groups and, if the routed set changed, applies it. If that
        fails, the previous set is applied again and nothing is stored: every
        user's DNS runs on this, so it is never left on a set that did not come
        up. → whether the routing was applied. Raises NotSaved."""
        acting = await self.__acting(groups)
        scoped = await self.__scoped(groups)
        entries = flat_entries(acting)
        if not upstreams_fit(entries, self.__routing.mode(), self.__upstream_bytes, direct_entries(acting)):
            raise NotSaved(self.__too_many(entries).error)
        changed = self.__routing.changed(acting, scoped)
        if changed:
            try:
                await self.__routing.follow(acting, scoped)
            except NotApplied as failed:
                try:
                    await self.__routing.follow(await self.__acting(old), await self.__scoped(old))
                except NotApplied as not_back:
                    raise NotSaved(f"{failed} — и прежний набор не вернулся: {not_back}") from None
                raise NotSaved(f"{failed} — возвращён прежний набор") from None
        await self.__store.save(groups)
        await self.__follow_blocking()
        self.__nudge_profiles()
        if changed:
            # entries typed by the admin are resolved right away; a list by link is
            # thousands of names, and those are learned as users ask for them
            await self.__routing.prewarm([*typed_entries(acting), *typed_scoped(scoped)])
        return changed

    async def save(self, request: SitesSet) -> SitesShown:
        """The groups as the page sends them: {"groups": […], "base": the fingerprint the page showed}."""
        async with file_lock(self.__lock_file):
            old = await self.__store.read()
            data = decode(request.payload)
            if data.get("base") != version_of_sites(old):
                raise Refused("сайты изменились в другом окне — показан новый список", conflict=True, **(await self.__shown(old)).shape())
            try:
                checked = checked_groups(data.get("groups"), old, self.__make_id, lists=True)
            except ValueError as e:
                raise Refused(str(e)) from None
            kept = {group.id for group in checked.groups}
            lost = [group.name for group in old if group.auto and group.id not in kept]
            if lost:
                raise Refused(f"группу «{lost[0]}» нельзя удалить: её сети обновляются сами. Её можно выключить.")
            groups: list[SiteGroup] = []
            for group in checked.groups:
                if group.id not in checked.new_lists:
                    groups.append(group)
                    continue
                try:
                    groups.append(await self.__downloaded(group))
                except ListFailed as e:
                    raise Refused(f"{group.name}: {e}") from None
            try:
                applied = await self.__store_and_apply(groups, old)
            except NotSaved as e:
                raise Refused(str(e)) from None
            return await self.__shown(groups, applied=applied)

    async def reapply(self) -> bool:
        """The routing made to follow the stored list (after a change of routes or order of the rules, rules/service.py).
        → whether it had to be applied. Raises Refused when it did not come up (the caller puts the old list back).
        The caller holds the lock of the sites (the same file): a save or the nightly refresh waits for the whole change."""
        stored = await self.__store.read()
        acting = await self.__acting(stored)
        scoped = await self.__scoped(stored)
        entries = flat_entries(acting)
        if not upstreams_fit(entries, self.__routing.mode(), self.__upstream_bytes, direct_entries(acting)):
            raise self.__too_many(entries)
        if not await self.__groups_fit():
            raise Refused("слишком много адресов для группы людей: блокировщик столько не принимает")
        if not self.__routing.changed(acting, scoped):
            return False
        try:
            await self.__routing.follow(acting, scoped)
        except NotApplied as failed:
            raise Refused(str(failed)) from None
        await self.__routing.prewarm([*typed_entries(acting), *typed_scoped(scoped)])
        return True

    async def __refreshed(self, group: SiteGroup, unattended: bool) -> SiteGroup:
        """A list by link downloaded again. One that cannot be downloaded keeps the entries it had and shows why."""
        had = len(group.items)
        try:
            fresh = await self.__downloaded(group)
        except ListFailed as e:
            return group.model_copy(update={"error": str(e)})
        if unattended and had >= TRUSTED_WITHOUT_LOOKING and len(fresh.items) < had / 2:
            # the unattended run does not trust that
            return group.model_copy(update={"error": f"список резко уменьшился: было {had}, стало {len(fresh.items)}"})
        return fresh

    async def refresh(self, request: SitesRefresh) -> SitesShown:
        """Downloads the lists by link again — one of them, or (the nightly job)
        every one that is switched on — and applies what changed."""
        only = request.list_id
        async with file_lock(self.__lock_file):
            old = await self.__store.read()
            targets = {group.id for group in old if group.by_link and (group.id == only if only else group.on)}
            if only and not targets:
                raise Refused("такого списка нет")
            groups: list[SiteGroup] = []
            for group in old:
                groups.append(await self.__refreshed(group, unattended=not only) if group.id in targets else group)
            try:
                applied = await self.__store_and_apply(groups, old)
            except NotSaved as e:
                reason = str(e)
                marked = [group.model_copy(update={"error": reason[:SHOWN_STORED_ERROR]}) if group.id in targets else group for group in old]
                await self.__store.save(marked)
                raise Refused(reason) from None
            return await self.__shown(groups, applied=applied)

    # ── what goes abroad at all ──────────────────────────────────────────────

    async def __switch_back(self, running: RouteMode, entries: Sequence[str]) -> str | None:
        """The mode that ran before, applied again. → why that failed, or None."""
        try:
            await self.__routing.switch(running, entries)
        except NotApplied as e:
            return str(e)
        return None

    async def set_mode(self, request: ModeSet) -> SitesShown:
        """The admin's switch between "only the list goes abroad" and "everything
        foreign goes abroad". A mode that did not come up is switched back, and
        only a mode that is running is stored."""
        mode = request.mode
        async with file_lock(self.__lock_file):
            running = self.__routing.mode()
            stored = await self.__modes.read()
            stored_groups = await self.__store.read()
            groups = await self.__acting(stored_groups)
            if mode == running == stored:
                return await self.__shown(stored_groups, already=True)
            # the file and the store disagreeing means a switch was cut short (or the
            # database was restored): what runs is then unknown, so it is applied anew
            entries = flat_entries(groups)
            if not upstreams_fit(entries, mode, self.__upstream_bytes, direct_entries(groups)):
                raise self.__too_many(entries)
            try:
                await self.__routing.switch(mode, entries)
            except NotApplied as failed:
                not_back = await self.__switch_back(running, entries)
                raise Refused(f"{failed} — и прежний режим не вернулся: {not_back}" if not_back else f"{failed} — возвращён прежний режим") from None
            try:
                await self.__modes.save(mode)
            except StoreDown:
                await self.__switch_back(running, entries)
                raise
            self.__nudge_profiles()
            if mode is RouteMode.LIST and mode != running:
                await self.__routing.prewarm(typed_entries(groups))
            return await self.__shown(stored_groups)

    # ── questions about one site ─────────────────────────────────────────────

    async def __blocked(self, host: str) -> Blocked | None:
        try:
            return await blocked_by(self.__blocker, self.__block_groups, host)
        except Exception:
            return None                 # the routing answer is still useful without the blocker

    async def check(self, request: Check) -> Checked:
        """Where would this name go? The smart DNS matches entries exactly; the VPN
        side (the resolver) only knows "domain and subdomains", so there a pattern
        acts as the domain it was widened to — the answer says so instead of hiding it."""
        host = request.host.strip().lower().rstrip(".")
        if not DOMAIN_RE.match(host):
            raise Refused("некорректное имя")
        vpn = Route(via=Via.DIRECT)
        dns = Route(via=Via.DIRECT)
        if self.__routing.mode() is not RouteMode.LIST:
            vpn = Route(via=Via.EXIT, group=None, rule=None)
            dns = vpn
        groups = await self.__acting(await self.__store.read())      # in the order of the rules, what acts
        # a group sent directly is asked first: its names leave from here, whatever the mode or the other groups
        for group in groups:
            if not (group.on and group.direct):
                continue
            for entry in group.items:
                if covers(entry, host):
                    direct = Route(via=Via.DIRECT, group=group.name, rule=entry)
                    return Checked(host=host, vpn=direct, dns=direct, blocked=await self.__blocked(host))
        for group in groups:
            if not group.on or group.direct:
                continue
            for entry in group.items:
                if is_network(entry):
                    continue
                if dns.via is Via.DIRECT and covers(entry, host):
                    dns = Route(via=Via.EXIT, group=group.name, rule=entry)
                wide = next(iter(resolver_names([entry])), None)
                if vpn.via is Via.DIRECT and wide and covers(wide, host):
                    vpn = Route(via=Via.EXIT, group=group.name, rule=wide)
        return Checked(host=host, vpn=vpn, dns=dns, blocked=await self.__blocked(host))

    async def probe(self, request: Probe) -> Probed:
        """Does this site open directly from here, and through each exit? For the
        admin's "it does not open" moments: one answer instead of guessing."""
        host = request.host.strip().lower().rstrip(".")
        path = DEFAULT_PAGE if request.path is None else request.path
        if not DOMAIN_RE.match(host) or not PROBE_PATH_RE.match(path):
            raise Refused("некорректное имя")
        try:
            found = await asyncio.to_thread(socket.getaddrinfo, host, HTTPS_PORT, socket.AF_INET, socket.SOCK_STREAM)
            address = str(found[0][4][0])
        except OSError:
            raise Refused("такого адреса нет") from None
        if not ipaddress.ip_address(address).is_global:
            raise Refused("это внутренний адрес")
        ways = ways_of(await asyncio.to_thread(text_or_empty, self.__cascade_conf))
        seen = await asyncio.gather(*(self.__prober.probe(host, address, way.source, path) for way in ways))
        return Probed(host=host, ways=[WaySeen(way=way, seen=one) for way, one in zip(ways, seen)])
