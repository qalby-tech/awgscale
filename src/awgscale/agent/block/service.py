"""The commands about what is blocked: block-get, block-set, block-lists."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from ..adguard.api import CHANGE_LIST_TIMEOUT, AdGuard
from ..adguard.client import AdGuardDown
from ..adguard.models import FilterList
from ..commands import BlockGet, BlockLists, BlockSet
from ..errors import Refused
from ..payloads import decode
from ..ports import BlockCounters, BlockGroupsStore, GroupMembers, RulesView, StoppedByList
from ..rules.compile import block_covered, shown_covered, without
from ..rules.scoped import is_scoped_route
from ..rules.store import Part, Way, blocks_of, scope_of, way_of
from ..sites.entries import covers, is_network
from ..users.models import KnownUser
from ..store.documents import Record
from ..users.models import version_of
from .entries import clean_groups, clean_label
from .links import clean_list_url, host_is_public
from .models import BLOCK_KIND, LIST_TITLES, MAX_BLOCK_LISTS, SAFE_BROWSING_TITLE, BlockShown, ListChange, ListShown, SafeBrowsingShown, is_allow_group

SHOWN_REASON = 120
LIST_NAME_LENGTH = 40
SHOWN_TOP = 5
REFRESH_ALL_TIMEOUT = 120.0
COMMENT_MARKS = ("!", "#")

IsPublic = Callable[[str], Awaitable[bool]]


def rules_in_file(path: Path) -> int:
    """The rules of a list AdGuard has downloaded: its lines that are neither empty nor comments."""
    try:
        with open(path, errors="replace") as handle:
            return sum(1 for line in handle if line.strip() and not line.lstrip().startswith(COMMENT_MARKS))
    except OSError:
        return 0


def lifted_by(rules: Sequence[Record]) -> dict[str, list[tuple[str, list[str]]]]:
    """{block rule id: [(an entry of a rule for some groups above it that sends it abroad or directly, those groups)]}."""
    above: list[tuple[str, list[str]]] = []
    lifting: dict[str, list[tuple[str, list[str]]]] = {}
    for rule in rules:
        if not rule.get("on"):
            continue
        if way_of(rule) is Way.BLOCK:
            if above:
                lifting[str(rule.get("id"))] = list(above)
            continue
        if is_scoped_route(rule):
            above.extend((entry, scope_of(rule)) for entry in rule.get("items") or [] if isinstance(entry, str) and not is_network(entry))
    return lifting


def title_of(filter_list: FilterList) -> Any:
    return LIST_TITLES.get(filter_list.url, filter_list.name)


def is_custom(filter_list: FilterList) -> bool:
    return filter_list.url not in LIST_TITLES


class BlockService:
    def __init__(
        self,
        adguard: AdGuard,
        groups: BlockGroupsStore,
        counters: BlockCounters,
        tops: StoppedByList,
        filters_dir: Path,
        is_public: IsPublic = host_is_public,
        rules: RulesView | None = None,
        members: GroupMembers | None = None,
    ) -> None:
        self.__rule_list = rules                # the whole list: a rule that routes, above a blocked one, unblocks
        self.__members = members                # the addresses of the people of a group: a rule for some groups blocks only them
        self.__last_lines: list[str] | None = None
        self.__adguard = adguard
        self.__groups = groups
        self.__counters = counters
        self.__tops = tops
        self.__filters_dir = filters_dir
        self.__is_public = is_public

    # ── showing ──────────────────────────────────────────────────────────────

    async def __rules(self, filter_list: FilterList) -> int:
        """Size of a block list. AdGuard reports 0 for a list that is switched off, so the file it downloaded is counted instead."""
        if filter_list.rules_count:
            return filter_list.rules_count
        try:
            path = self.__filters_dir / f"{int(filter_list.id)}.txt"
        except (TypeError, ValueError):
            return 0
        return await asyncio.to_thread(rules_in_file, path)

    async def __list_shown(self, filter_list: FilterList, stopped: Mapping[str, Any]) -> ListShown:
        return ListShown(
            id=filter_list.id,
            title=title_of(filter_list),
            name=filter_list.name,
            url=filter_list.url,
            custom=is_custom(filter_list),
            on=filter_list.enabled,
            rules=await self.__rules(filter_list),
            updated=filter_list.last_updated,
            day=int(stopped.get(str(filter_list.id), 0)),
        )

    async def __shown(self) -> BlockShown:
        filters = await self.__adguard.filters()
        counters = await self.__counters.today()
        stopped = await self.__tops.stopped_by_list()
        lists = [await self.__list_shown(filter_list, stopped) for filter_list in filters]
        safebrowsing = SafeBrowsingShown(on=await self.__adguard.safebrowsing(), day=int(counters.safe))
        groups = await self.__groups.read()
        covered: dict[str, dict[str, str]] = {}
        if self.__rule_list is not None:
            ids = {str(group.get("id")) for group in groups}
            covered = {rule: entries for rule, entries in shown_covered(await self.__rule_list.read()).items() if rule in ids}
        return BlockShown(
            covered=covered,
            lists=lists,
            safebrowsing=safebrowsing,
            groups=groups,
            v=version_of(groups),
            top=list(counters.top_blocked[:SHOWN_TOP]),
            queries=counters.queries,
            blocked=counters.blocked,
        )

    async def show(self, _request: BlockGet | None = None) -> BlockShown:
        try:
            return await self.__shown()
        except Exception as e:
            raise Refused(f"блокировщик не отвечает: {str(e)[:SHOWN_REASON]}") from None

    # ── the admin's own groups, the switches of the lists ────────────────────

    async def __apply(self, data: Mapping[str, Any], groups: Sequence[Record]) -> None:
        switches = data.get("lists")
        wanted_switches = switches if isinstance(switches, dict) else {}
        refresh = False
        for filter_list in await self.__adguard.filters():
            key = str(filter_list.id)
            if key not in wanted_switches or bool(wanted_switches[key]) == filter_list.enabled:
                continue
            await self.__adguard.set_filter(filter_list, filter_list.name, bool(wanted_switches[key]))
            refresh = refresh or bool(wanted_switches[key])
        if refresh:                                 # a list that was never on has no rules yet
            await self.__adguard.refresh()
        if "safebrowsing" in data:
            wanted_on = bool(data["safebrowsing"])
            if wanted_on != await self.__adguard.safebrowsing():
                await self.__adguard.set_safebrowsing(wanted_on)
        await self.__set_rules(groups)

    async def __compiled(self, groups: Sequence[Record], rules_now: Sequence[Record] | None, users: Sequence[KnownUser] | None) -> list[str]:
        """The agent owns AdGuard's custom rules: every entry of a group that is on becomes ||name^ (AdGuard
        understands `*` in it) — but an entry a rule above names is left to that rule (rules/compile.py), and a
        rule for some groups of people blocks only their addresses: ||name^$client='172.16.17.5'|'172.16.17.9'.
        A rule for some groups that sends a name abroad or directly, above, unblocks it for its people only:
        ||name^$client=~'172.16.17.5' (only this rule is lifted for them, not the block lists)."""
        whole: Sequence[Record] = []
        if self.__rule_list is not None:
            whole = rules_now if rules_now is not None else await self.__rule_list.preview(Part.BLOCK, groups)
            groups = without(blocks_of(whole), block_covered(whole))
        lifting = lifted_by(whole)
        scoped = [group for group in groups if group["on"] and scope_of(group)]
        addresses: dict[str, list[str]] = {}
        if (scoped or lifting) and self.__members is not None:
            addresses = await self.__members.addresses(users)

        def ips_of(group_ids: Iterable[str]) -> set[str]:
            return {ip for group_id in group_ids for ip in addresses.get(group_id, [])}

        lines: list[str] = []
        for group in groups:
            if not group["on"]:
                continue
            scope = scope_of(group)
            lifts = lifting.get(str(group.get("id")), [])
            for entry in group["items"]:
                spared = ips_of(group_id for above, group_ids in lifts if covers(above, entry) for group_id in group_ids)
                if not scope:
                    if spared:
                        lines.append("||" + entry + "^$client=" + "|".join(f"~'{ip}'" for ip in sorted(spared)))
                    else:
                        lines.append("||" + entry + "^")
                    continue
                ips = sorted(ips_of(scope) - spared)
                if ips:                               # a rule for groups that have nobody now blocks nobody
                    lines.append("||" + entry + "^$client=" + "|".join(f"'{ip}'" for ip in ips))
        return lines

    async def __set_rules(self, groups: Sequence[Record], rules_now: Sequence[Record] | None = None, users: Sequence[KnownUser] | None = None) -> None:
        lines = await self.__compiled(groups, rules_now, users)
        await self.__adguard.set_rules(lines)
        self.__last_lines = lines

    async def follow(self, users: Sequence[KnownUser] | None = None, force: bool = True) -> None:
        """The blocker's rules made again from the list as it is stored (after a change of the rules above, or of who
        is in the groups a rule is for). Without `force` (the look at the state, every few seconds) the blocker is
        told only when the rules came out otherwise than last time."""
        if self.__rule_list is None:
            return
        whole = await self.__rule_list.read()
        if not force:
            lines = await self.__compiled(blocks_of(whole), whole, users)
            if lines == self.__last_lines:
                return
        await self.__set_rules(blocks_of(whole), whole, users)

    async def save(self, request: BlockSet) -> BlockShown:
        """{"groups": […], "base": the fingerprint the page showed (the agent's own moves send none), …}."""
        data = decode(request.payload)
        stored = await self.__groups.read()
        base = data.get("base")
        if isinstance(base, str) and base and base != version_of(stored):
            raise Refused("блокировки изменились в другом окне — показан новый список", conflict=True, **{key: held for key, held in (await self.show()).wire().items() if key != "ok"})
        raw = data.get("groups")
        if isinstance(raw, list):                   # a page loaded before the change may still send «Исключения»
            raw = [group for group in raw if not is_allow_group(group)]
        groups = clean_groups(raw, stored, BLOCK_KIND)
        for group in groups:
            group["kind"] = BLOCK_KIND
        self.__groups.check(groups)                 # refused here, before the blocker is changed for a save that would not be kept
        try:
            await self.__apply(data, groups)
        except Exception as e:
            raise Refused(f"блокировщик не принял изменения: {str(e)[:SHOWN_REASON]}") from None
        await self.__groups.save(groups)
        return await self.show()

    # ── block lists by link ──────────────────────────────────────────────────

    async def __add_list(self, data: Mapping[str, Any], filters: Sequence[FilterList], taken: set[str]) -> None:
        name = clean_label(data.get("name"), LIST_NAME_LENGTH)
        link = clean_list_url(data.get("url"))
        if not name:
            raise Refused("название списка: от 1 до 40 символов")
        if link is None:
            raise Refused("нужна ссылка на файл списка, начинается с https://")
        if any(filter_list.url == link.url for filter_list in filters):
            raise Refused("этот список уже подключён")
        if name.lower() in taken:
            raise Refused("список с таким названием уже есть")
        if len([filter_list for filter_list in filters if is_custom(filter_list)]) >= MAX_BLOCK_LISTS:
            raise Refused(f"не больше {MAX_BLOCK_LISTS} списков по ссылке")
        if not await self.__is_public(link.host):
            raise Refused("адрес из ссылки не найден")
        try:
            await self.__adguard.add_filter(name, link.url)
        except AdGuardDown as e:
            raise Refused("список не подключён: " + e.said) from None

    async def __change_list(self, change: ListChange, data: Mapping[str, Any], filters: Sequence[FilterList], taken: set[str]) -> None:
        list_id = data.get("id")
        chosen = next((filter_list for filter_list in filters if is_custom(filter_list) and type(list_id) is int and filter_list.id == list_id), None)
        if chosen is None:
            raise Refused("такого списка нет")
        try:
            if change is ListChange.REMOVE:
                await self.__adguard.remove_filter(chosen)
                return
            name = clean_label(data.get("name"), LIST_NAME_LENGTH)
            if not name:
                raise Refused("название списка: от 1 до 40 символов")
            if name.lower() in taken - {str(chosen.name).lower()}:
                raise Refused("список с таким названием уже есть")
            await self.__adguard.set_filter(chosen, name, chosen.enabled, CHANGE_LIST_TIMEOUT)
        except AdGuardDown as e:
            raise Refused("не получилось: " + e.said) from None

    async def change_lists(self, request: BlockLists) -> BlockShown:
        """{"op": "add", "name", "url"} | {"op": "remove", "id"} | {"op": "rename", "id", "name"} | {"op": "refresh"}"""
        data = decode(request.payload)
        try:
            filters = await self.__adguard.filters()
        except Exception as e:
            raise Refused(f"блокировщик не отвечает: {str(e)[:SHOWN_REASON]}") from None
        taken = {str(title_of(filter_list)).lower() for filter_list in filters} | {SAFE_BROWSING_TITLE.lower()}
        try:
            change = ListChange(data.get("op"))
        except ValueError:
            raise Refused("некорректные данные") from None
        if change is ListChange.ADD:
            await self.__add_list(data, filters, taken)
        elif change is ListChange.REFRESH:
            try:
                await self.__adguard.refresh(REFRESH_ALL_TIMEOUT)
            except AdGuardDown as e:
                raise Refused("списки не обновились: " + e.said) from None
        else:
            await self.__change_list(change, data, filters, taken)
        return await self.show()
