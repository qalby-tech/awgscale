#!/usr/bin/env python3
"""The edges of the agent's package that the suites of whole commands do not
reach: refusals, a blocker or a store that is away, a file that is not there,
a look that comes late. Each is asked of the package's own class or function,
with stand-ins written for the one case.

    uv run python tests/agent_edges.py

Nothing live is touched."""
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from awgscale.core.ashell import TIMED_OUT
from awgscale.core.shell import Result

from awgscale.agent.adguard.profiles import AdGuardProfiles, ProfileMemory
from awgscale.agent.cascade import conf
from awgscale.agent.cascade.models import checked
from awgscale.agent.clients.exits import Registry
from awgscale.agent.commands import ExitAdd, InviteAdd, InviteRedeem, InviteRemove, ModeSet, Probe, RouteMode
from awgscale.agent.errors import Refused
from awgscale.agent.exits.service import ExitServers
from awgscale.agent.exits.working import CompiledExits
from awgscale.agent.invites.service import InvitesService
from awgscale.agent.invites.store import unexpired
from awgscale.agent.ports import Outcome
from awgscale.agent.protocol import Caller
from awgscale.agent.seen.service import SeenNotes
from awgscale.agent.sites.models import SiteGroup
from awgscale.agent.sites.probe import CURL_TIMED_OUT, CurlProber
from awgscale.agent.sites.routing import listed_lines
from awgscale.agent.sites.service import SitesService
from awgscale.agent.users.models import KnownUser, StoredGroups, UserGroup
from awgscale.agent.users.pauses import Pauses
from awgscale.agent.users.pins import Pinning
from awgscale.agent.users.regrouping import chosen_kids_lists, invitations_left

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="agent-edges-")
LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
GONE = "Ссылка недействительна или уже использована"


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:800])


def run(work):
    return LOOP.run_until_complete(work)


def refusal(work):
    """The text of the refusal, or None when the work did not refuse."""
    try:
        run(work)
    except Refused as e:
        return e.error
    return None


def b64(value):
    return base64.b64encode(json.dumps(value, ensure_ascii=False).encode()).decode()


def place(name):
    return Path(TMP) / name


class Journal:
    def __init__(self):
        self.lines = []

    async def write(self, text, bad=False, kind=None, who=None):
        self.lines.append({"text": text, "bad": bad, "kind": getattr(kind, "value", kind), "who": getattr(who, "value", who)})


class Groups:
    def __init__(self, records):
        self.records = records
        self.broken = False

    async def read(self):
        return StoredGroups.of(json.loads(json.dumps(self.records)))

    async def save(self, records):
        if self.broken:
            raise OSError("store away")
        self.records = json.loads(json.dumps(records))


# ── the per-group filtering ─────────────────────────────────────────────────
def t_profiles():
    print("\n— фильтрация групп: блокировщик отказывает, молчит, совпадает")
    kids = {"id": "kkkkk1", "name": "Дети", "filter": "kids", "members": ["bob"]}
    users = [KnownUser(name="bob", ip="10.9.9.3")]

    class Clients:
        def __init__(self):
            self.have = {}
            self.refuse = False
            self.down = False
            self.asked = 0

        async def held(self):
            self.asked += 1
            if self.down:
                raise OSError("refused")
            return dict(self.have)

        async def add(self, profile):
            if self.refuse:
                return Outcome(ok=False, said="clients/add: 400")
            self.have[profile.name] = profile.wire()
            return Outcome(ok=True)

        async def update(self, name, profile):
            if self.refuse:
                return Outcome(ok=False, said="clients/update: 400")
            self.have.pop(name, None)
            self.have[profile.name] = profile.wire()
            return Outcome(ok=True)

        async def delete(self, name):
            if self.refuse:
                return Outcome(ok=False, said="clients/delete: 400")
            self.have.pop(name, None)
            return Outcome(ok=True)

    clients = Clients()
    journal = Journal()
    memory = ProfileMemory()
    profiles = AdGuardProfiles(clients, Groups([kids]), journal, place("profiles.lock"), memory)

    run(profiles.check([]))
    ok(clients.asked == 0 and memory.signature is None, "список пользователей не прочитан — блокировщик не спрашивается и ничего не меняется", clients.asked)

    clients.down = True
    run(profiles.check(users))
    run(profiles.check(users))
    ok(clients.asked == 1 and memory.signature is None, "блокировщик не ответил — следующий опрос его не ждёт (спрошен один раз)", clients.asked)

    clients.down = False
    memory.failed_at = None
    clients.have = {"grp-kkkkk1": {"name": "grp-kkkkk1", "ids": ["10.9.9.3"], "filtering_enabled": False}}
    clients.refuse = True
    run(profiles.check(users))
    memory.asked_at = None
    run(profiles.check(users))
    ok(len(journal.lines) == 1 and journal.lines[0]["text"].startswith("Фильтрация групп не применяется") and journal.lines[0]["bad"] is True and journal.lines[0]["who"] == "system",
       "profiles_check: отказ блокировщика попадает в журнал один раз, а не с каждым опросом", journal.lines)
    ok("clients/update: 400" in journal.lines[0]["text"] and "grp-kkkkk1" in clients.have, "в строке журнала — что сказал блокировщик; отказанный клиент остался как был", journal.lines)

    clients.refuse = False
    memory.asked_at = None
    run(profiles.check(users))
    clients.asked = 0
    fresh = ProfileMemory()
    restarted = AdGuardProfiles(clients, Groups([kids]), journal, place("profiles.lock"), fresh)
    run(restarted.check(users))
    ok(clients.asked == 1 and fresh.signature is not None and fresh.asked_at is not None and len(clients.have) == 1,
       "после перезапуска агента: блокировщик спрошен, всё совпало — ничего не меняется, это запомнено", [clients.asked, fresh.signature])


def UserGroupOf(record):
    return UserGroup.of(record)


def t_profiles_routes():
    print("\n— клиенты групп с правилами «за границу»/«напрямую»: меняются на месте, а не помещающийся — как у всех")
    from awgscale.agent.adguard.api import route_variant
    from awgscale.agent.adguard.profiles import CLIENT_REQUEST_BYTES, request_bytes
    from awgscale.agent.sites.scoped import RouteDns
    kids = {"id": "kkkkk1", "name": "Дети", "filter": "kids", "members": ["bob", "eve"]}
    home = {"id": "hhhhh1", "name": "Дом", "filter": "std", "members": ["ann"]}
    users = [KnownUser(name="bob", ip="10.9.9.3"), KnownUser(name="eve", ip="10.9.9.4"), KnownUser(name="ann", ip="10.9.9.5")]

    class Clients:
        def __init__(self):
            self.have = {}
            self.calls = []
            self.refuse = set()

        async def held(self):
            return json.loads(json.dumps(self.have))

        def __taken(self, name, ids):
            return any(set(ids) & set(client["ids"]) for other, client in self.have.items() if other != name)

        async def add(self, profile):
            self.calls.append(("add", profile.name, list(profile.ids)))
            if "add" in self.refuse or self.__taken(profile.name, profile.ids) or request_bytes(profile) > 65536:
                return Outcome(ok=False, said="clients/add: 400")
            self.have[profile.name] = profile.wire()
            return Outcome(ok=True)

        async def update(self, name, profile):
            self.calls.append(("update", name, list(profile.ids)))
            if "update" in self.refuse or name not in self.have or self.__taken(name, profile.ids) or request_bytes(profile) > 65536:
                return Outcome(ok=False, said="clients/update: 400")
            del self.have[name]
            self.have[profile.name] = profile.wire()
            return Outcome(ok=True)

        async def delete(self, name):
            self.calls.append(("delete", name))
            self.have.pop(name, None)
            return Outcome(ok=True)

    class Dns:
        def __init__(self):
            self.routes = {}

        async def of(self):
            return dict(self.routes)

    clients = Clients()
    journal = Journal()
    memory = ProfileMemory()
    dns = Dns()
    groups = Groups([kids, home])
    class Lists:
        nudged = 0

        def nudge(self, groups, own=None, carried=None):
            Lists.nudged += 1

        async def in_force(self):
            from awgscale.agent.adguard.group_lists import ListsInForce
            return ListsInForce(loaded=frozenset())

        def settled(self, in_force):
            pass

        guard = asyncio.Lock()

    profiles = AdGuardProfiles(clients, groups, journal, place("routes.lock"), memory, group_lists=Lists(), dns=dns)
    sync = lambda: run(profiles.sync([UserGroupOf(kids), UserGroupOf(home)], users))
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8", "[/a.example/]1.1.1.1"), direct_lines=()), "hhhhh1": RouteDns(upstreams=("8.8.8.8",), direct_lines=())}
    ok(sync() is None and set(clients.have) == {route_variant("grp-kkkkk1", "kkkkk1"), "grp-rhhhhh1"}, "группы с правилами: дети — в варианте своего клиента, «Дом» — в «grp-r…»", sorted(clients.have))
    clients.calls.clear()
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8", "[/a.example/]1.1.1.1", "[/b.example/]1.1.1.1"), direct_lines=()), "hhhhh1": dns.routes["hhhhh1"]}
    ok(sync() is None and clients.calls == [("update", route_variant("grp-kkkkk1", "kkkkk1"), ["10.9.9.3", "10.9.9.4"])],
       "у группы изменился список (правка «Сайтов», ночное обновление): один clients/update, ни удаления, ни добавления — люди всё время в своём клиенте", clients.calls)
    before = json.loads(json.dumps(clients.have))
    clients.calls.clear()
    clients.refuse = {"update"}
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8",), direct_lines=()), "hhhhh1": dns.routes["hhhhh1"]}
    error = sync()
    clients.refuse = set()
    ok(error and clients.have == before and not [call for call in clients.calls if call[0] == "delete"],
       "блокировщик отказал в изменении: клиенты остались как были, ничего не удалено", [error, clients.calls])
    clients.calls.clear()
    kids_moved = dict(kids, members=["bob"])
    home_grown = dict(home, members=["ann", "eve"])
    run(profiles.sync([UserGroupOf(kids_moved), UserGroupOf(home_grown)], users))
    ok(clients.have[route_variant("grp-kkkkk1", "kkkkk1")]["ids"] == ["10.9.9.3"] and clients.have["grp-rhhhhh1"]["ids"] == ["10.9.9.4", "10.9.9.5"]
       and clients.calls == [("update", "grp-rhhhhh1", ["10.9.9.4/32", "10.9.9.5"]), ("update", route_variant("grp-kkkkk1", "kkkkk1"), ["10.9.9.3"]),
                             ("update", "grp-rhhhhh1", ["10.9.9.4", "10.9.9.5"])],
       "человек перешёл из группы в группу, оба клиента остаются: тот, что берёт, сначала держит его как сеть /32, потом тот, что отдаёт, "
       "его отпускает (и в том же вызове берёт свой новый список), потом он — сам адрес; ни на миг не вне клиента", clients.calls)
    huge = tuple(f"[/site{number}.example.net/]1.1.1.1 9.9.9.9" for number in range(1800))
    clients.calls.clear()
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8", *huge), direct_lines=()), "hhhhh1": dns.routes["hhhhh1"]}
    ok(sync() is None and route_variant("grp-kkkkk1", "kkkkk1") not in clients.have and clients.have.get("grp-kkkkk1", {}).get("upstreams") == []
       and memory.degraded == frozenset({"kkkkk1"}) and "grp-rhhhhh1" in clients.have
       and clients.calls[:2] == [("add", "grp-kkkkk1", ["10.9.9.3/32", "10.9.9.4/32"]), ("delete", route_variant("grp-kkkkk1", "kkkkk1"))],
       f"список детей не помещается в один запрос (> {CLIENT_REQUEST_BYTES} байт с адресами): их клиент спрашивает как все — детский клиент без списка сначала "
       "держит детей как сети /32, и только потом вариант удаляется (детская фильтрация ни на миг не снята)",
       [sorted(clients.have), clients.calls])
    ok(Lists.nudged and all(client.get("tags") == [] for client in clients.have.values()),
       "хранитель списков подтолкнут; списки — метки самих клиентов (у детей без списков их нет), их варианту называть нечего", [Lists.nudged, clients.have])
    said = [line for line in journal.lines if "не помещается" in line["text"]]
    sync()
    ok(len(said) == 1 and said[0]["bad"] and "«Дети»" in said[0]["text"] and len([line for line in journal.lines if "не помещается" in line["text"]]) == 1,
       "в журнале — один раз, а не с каждым опросом", journal.lines)
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8",), direct_lines=()), "hhhhh1": dns.routes["hhhhh1"]}
    clients.calls.clear()
    ok(sync() is None and route_variant("grp-kkkkk1", "kkkkk1") in clients.have and memory.degraded == frozenset()
       and clients.calls[:2] == [("add", route_variant("grp-kkkkk1", "kkkkk1"), ["10.9.9.3/32", "10.9.9.4/32"]), ("delete", "grp-kkkkk1")]
       and any("снова" in line["text"] for line in journal.lines), "список снова помещается: клиент группы вернулся сам (сначала держит детей как сети /32), в журнале — что вернулся",
       [sorted(clients.have), clients.calls])
    many = [KnownUser(name=f"u{number}", ip=f"10.{number // 250 + 10}.{number % 250}.2") for number in range(2600)]
    crowd = dict(home, members=[user.name for user in many])
    dns.routes = {"hhhhh1": RouteDns(upstreams=("8.8.8.8", *huge[:900]), direct_lines=())}
    fit_alone = run(profiles.sync([UserGroupOf(crowd)], many))
    ok(fit_alone is None and "grp-rhhhhh1" not in clients.have and memory.degraded == frozenset({"hhhhh1"}),
       "группа выросла: список влезал, а с адресами её людей — нет; её люди — как все, и блокировщик ничего не отверг", [fit_alone, sorted(clients.have)])


# AdGuard's own tags (v0.107.80, `GET control/clients` supported_tags; tests/stack_adguard.py checks the list)
SUPPORTED_TAGS = frozenset({"device_audio", "device_camera", "device_gameconsole", "device_laptop", "device_nas", "device_other", "device_pc", "device_phone",
                            "device_printer", "device_securityalarm", "device_tablet", "device_tv", "os_android", "os_ios", "os_linux", "os_macos", "os_other",
                            "os_windows", "user_admin", "user_child", "user_regular"})
LIST_RULE = re.compile(r"^\|\|(?P<name>[^\^]+)\^(?:\$(?P<kind>ctag|client)=(?P<values>.+))?$")


def list_text(source):
    """The stand-in download of one of HaGeZi's lists: a name only it has (its marker: whether it blocks for someone is
    whether they have the list), names some other lists have too, one every list has."""
    from awgscale.agent.adguard.group_lists import GroupSource
    order = list(GroupSource)
    index = order.index(source)
    names = [f"marker-{source.value}.test", "everywhere.test", f"pair-{index // 2}.test", *(f"only-{source.value}-{number}.test" for number in range(3))]
    return "[Adblock Plus]\n! Title: stand-in\n" + "".join(f"||{name}^\n" for name in names)


class StrictAdGuard:
    """AdGuard's clients and block lists as v0.107.80 keeps them (tests/stack_adguard.py checks each of these on the real
    binary): an address and a network ("10.0.0.2/32") are held apart, a person is filtered by the client of their address
    first and by the one of their network after it (also when the client of the address is deleted); an address or a
    network another client holds, a client with nothing in it, a name taken or missing, a request over `limit` bytes, a
    tag AdGuard does not know — refused. Tags are read back sorted, a tag sent twice kept twice. A block list is AdGuard's
    own copy of its file, made when it is added and at a refresh (never when the file changes); a rule `$ctag=a|b` blocks
    for the clients that carry a or b, `$client='n'` for the client named n. With `failing`, a call fails at random:
    refused, or done with its answer lost (a timeout); `held` too; `filters_failing`, the same for the calls of the
    block lists. `seen` is called after every call, failed or not."""

    def __init__(self, rnd=None, failing=0.0, limit=65536):
        self.have = {}
        self.calls = []
        self.list_calls = []            # add_url, set_url, remove_url, refresh
        self.sized = []                 # every add and update as sent: (call, …, the client)
        self.invalid = []               # the calls AdGuard itself would refuse (not those refused on purpose): never any
        self.limit = limit              # AdGuard refuses a request over 65 536 bytes
        self.rnd = rnd
        self.failing = failing
        self.filters_failing = 0.0
        self.seen = None
        self.refuse_at = None           # the number of the call (from 0) that is refused, or answered and lost
        self.lose_at = None
        self.lists = []                 # {"url", "name", "enabled"}: the block lists
        self.copies = {}                # url → the text AdGuard has loaded of it
        self.updated = {}               # url → when AdGuard last read it (to the microsecond here; AdGuard says whole seconds)
        self.parsed = {}                # text → {name: [(kind, values)]}, of the last few texts

    @staticmethod
    def parts(ids):
        return {i for i in ids if "/" not in i}, {i for i in ids if "/" in i}

    def clash(self, ids, skip):
        exact, nets = self.parts(ids)
        for name, client in self.have.items():
            if name == skip:
                continue
            others_exact, others_nets = self.parts(client["ids"])
            if exact & others_exact or nets & others_nets:
                return True
        return False

    def serving(self, address):
        """The client that filters this address now, or None."""
        by_net = None
        for name, client in self.have.items():
            if address in client["ids"]:
                return name
            if address + "/32" in client["ids"]:
                by_net = name
        return by_net

    def rules(self, text):
        key = text
        if key not in self.parsed:
            table = {}
            for line in text.splitlines():
                found = LIST_RULE.match(line)
                if found:
                    values = (found.group("values") or "").replace("'", "").split("|") if found.group("kind") else []
                    table.setdefault(found.group("name"), []).append((found.group("kind"), set(values)))
            self.parsed = {key: table, **{k: v for k, v in list(self.parsed.items())[-8:]}}
        return self.parsed[key]

    def blocks(self, address, name):
        """Does a loaded block list stop `name` for the person at this address?"""
        holder = self.serving(address)
        client = self.have.get(holder) if holder else None
        if client is not None and not client.get("use_global_settings") and not client.get("filtering_enabled"):
            return False
        for filter_list in self.lists:
            if not filter_list["enabled"] or filter_list["url"] not in self.copies:
                continue
            for kind, values in self.rules(self.copies[filter_list["url"]]).get(name, []):
                if kind is None:
                    return True
                if kind == "ctag" and client is not None and values & set(client.get("tags") or []):
                    return True
                if kind == "client" and (holder in values or address in values):
                    return True
        return False

    def lists_of(self, address):
        """The groups' lists the person at this address has now: those whose marker is blocked for them."""
        from awgscale.agent.adguard.group_lists import GroupSource
        return frozenset(source for source in GroupSource if self.blocks(address, f"marker-{source.value}.test"))

    def fate(self):
        number = len(self.calls) - 1
        if number == self.refuse_at:
            return "refused"
        if number == self.lose_at:
            return "lost"
        if self.failing and self.rnd.random() < self.failing:
            return self.rnd.choice(["refused", "lost"])
        return "done"

    def answer(self, valid, change):
        if not valid:
            self.invalid.append((self.calls[-1], json.loads(json.dumps(self.have))))
        fate = self.fate()
        if valid and fate != "refused":
            change()
        if self.seen:
            self.seen(self.calls[-1])
        return Outcome(ok=valid and fate == "done", said="" if valid and fate == "done" else f"{self.calls[-1][0]}: 400")

    async def held(self):
        await asyncio.sleep(0)
        if self.failing and self.rnd.random() < self.failing / 3:
            from awgscale.agent.adguard.client import AdGuardDown
            raise AdGuardDown("timed out", "timeout")
        return json.loads(json.dumps(self.have))

    def fits(self, profile):
        from awgscale.agent.adguard.moves import request_bytes
        return request_bytes(profile) <= self.limit

    @staticmethod
    def stored(profile):
        wire = profile.wire()
        wire["tags"] = sorted(wire.get("tags") or [])
        return wire

    async def add(self, profile):
        await asyncio.sleep(0)
        self.calls.append(("add", profile.name, list(profile.ids)))
        self.sized.append(("add", profile.name, profile.wire()))
        valid = (profile.name not in self.have and bool(profile.ids) and not self.clash(profile.ids, None) and self.fits(profile)
                 and set(profile.tags) <= SUPPORTED_TAGS)
        return self.answer(valid, lambda: self.have.__setitem__(profile.name, self.stored(profile)))

    async def update(self, name, profile):
        await asyncio.sleep(0)
        self.calls.append(("update", name, profile.name, list(profile.ids)))
        self.sized.append(("update", name, profile.name, profile.wire()))
        valid = (name in self.have and (profile.name == name or profile.name not in self.have) and bool(profile.ids) and not self.clash(profile.ids, name)
                 and self.fits(profile) and set(profile.tags) <= SUPPORTED_TAGS)

        def change():
            del self.have[name]
            self.have[profile.name] = self.stored(profile)
        return self.answer(valid, change)

    async def delete(self, name):
        await asyncio.sleep(0)
        self.calls.append(("delete", name))
        return self.answer(name in self.have, lambda: self.have.pop(name))

    # ── the block lists ──────────────────────────────────────────────────

    def list_fate(self, change):
        """A call of the block lists: done, refused (nothing changes), or done with its answer lost."""
        from awgscale.agent.adguard.client import AdGuardDown, AdGuardRefused
        fate = "done"
        if self.filters_failing and self.rnd.random() < self.filters_failing:
            fate = self.rnd.choice(["refused", "lost"])
        if fate != "refused":
            change()
        if self.seen:
            self.seen(self.list_calls[-1])
        if fate == "refused":
            raise AdGuardRefused(500, "Internal Server Error", "refused")
        if fate == "lost":
            raise AdGuardDown("timed out", "ReadTimeout")

    def read_file(self, url):
        """AdGuard reads a local list into its copy, and says it read it now (also when the file did not change)."""
        from datetime import UTC, datetime
        try:
            text = Path(url).read_text()
        except OSError:
            return
        self.updated[url] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self.copies[url] = text

    async def filters_now(self):
        from awgscale.agent.adguard.models import FilterList
        await asyncio.sleep(0)
        if self.filters_failing and self.rnd.random() < self.filters_failing / 3:
            from awgscale.agent.adguard.client import AdGuardDown
            raise AdGuardDown("timed out", "timeout")
        return [FilterList(id=index, url=f["url"], name=f["name"], enabled=f["enabled"], last_updated=self.updated.get(f["url"]),
                           rules_count=sum(1 for line in self.copies.get(f["url"], "").splitlines() if LIST_RULE.match(line)) if f["enabled"] else 0)
                for index, f in enumerate(self.lists)]

    async def filters(self):
        return await self.filters_now()

    async def others(self):
        """The admin's own clients: none here."""
        await asyncio.sleep(0)
        return {}

    async def add_filter(self, name, url):
        await asyncio.sleep(0)
        self.list_calls.append(("add_url", url))

        def change():
            self.lists.append({"url": url, "name": name, "enabled": True})
            self.read_file(url)
        self.list_fate(change)

    async def set_filter(self, filter_list, name, enabled, timeout=None):
        await asyncio.sleep(0)
        self.list_calls.append(("set_url", filter_list.url, enabled))

        def change():
            for f in self.lists:
                if f["url"] == filter_list.url:
                    if enabled and not f["enabled"] and f["url"].startswith("/"):
                        self.read_file(f["url"])        # a list switched on is read at once
                    f["enabled"] = enabled
        self.list_fate(change)

    async def remove_filter(self, filter_list):
        await asyncio.sleep(0)
        self.list_calls.append(("remove_url", filter_list.url))

        def change():
            self.lists = [f for f in self.lists if f["url"] != filter_list.url]
            self.copies.pop(filter_list.url, None)
        self.list_fate(change)

    async def refresh(self):
        await asyncio.sleep(0)
        self.list_calls.append(("refresh",))

        def change():
            for f in self.lists:
                if f["enabled"] and f["url"].startswith("/"):
                    self.read_file(f["url"])
        self.list_fate(change)


class MovesOwn:
    def __init__(self, choices):
        self.choices = dict(choices or {})

    async def read(self):
        return dict(self.choices)


class MovesWhole:
    def __init__(self, names):
        self.names = set(names)

    async def whole(self):
        return set(self.names)


class MovesDownloads:
    """HaGeZi as the keeper of the lists sees it: a list in `failing` cannot be downloaded, one in `hanging` never ends."""

    def __init__(self):
        self.failing = set()
        self.hanging = set()
        self.asked = []

    async def text(self, source):
        import httpx
        self.asked.append(source.value)
        await asyncio.sleep(0)
        if source in self.hanging:
            for _ in range(5):
                await asyncio.sleep(0)          # others go on meanwhile; then the keeper's deadline (agent_block checks it on a real clock) ends it
            raise asyncio.TimeoutError()
        if source in self.failing:
            raise httpx.ConnectError("no route to host")
        return list_text(source)


class MovesDns:
    def __init__(self, routes):
        self.routes = dict(routes or {})
        self.broken = False

    async def of(self):
        if self.broken:
            raise OSError("rules store away")
        return dict(self.routes)


MOVES_WORLDS = [0]


async def inline_thread(function, /, *args, **kwargs):
    """asyncio.to_thread for the model: the same work in the loop's own thread, so that a seed always draws the same
    interleaving of the keeper of the lists and the clients' changes."""
    await asyncio.sleep(0)
    return function(*args, **kwargs)


def moves_agent(world, lists_memory=None, profile_memory=None):
    """The agent of a world (started again: what it knew is gone, AdGuard and the file stay): the keeper of the
    groups' lists over the world's file, and the per-group clients."""
    from awgscale.agent.adguard.group_lists import GroupBlockLists, GroupListsMemory
    old = world.get("keeper")
    if old is not None and old.task is not None and not old.task.done():
        old.task.cancel()                                       # a process stopped halfway through its work
        try:
            run(old.task)
        except BaseException:
            pass
    world["lists_memory"] = lists_memory or GroupListsMemory()
    world["profile_memory"] = profile_memory or ProfileMemory()
    world["keeper"] = GroupBlockLists(world["adguard"], world["groups"], world["journal"], world["downloads"], world["file"], 1,
                                      world["lists_memory"], own=world["own"], deadline=0.002)
    world["profiles"] = AdGuardProfiles(world["adguard"], world["groups"], world["journal"], place("moves.lock"), world["profile_memory"],
                                        group_lists=world["keeper"], own=world["own"], routes=world["whole"], dns=world["dns"])


def moves_world(seed_groups, users, own=None, whole=(), routes=None):
    """AdGuardProfiles and the keeper of the groups' lists over a StrictAdGuard, with what the groups, own choices,
    whole-traffic people and rules are."""
    adguard = StrictAdGuard()
    groups = Groups(seed_groups)
    MOVES_WORLDS[0] += 1
    folder = place(f"lists-{MOVES_WORLDS[0]}")
    world = {"adguard": adguard, "groups": groups, "own": MovesOwn(own), "whole": MovesWhole(whole), "dns": MovesDns(routes), "users": users,
             "downloads": MovesDownloads(), "file": folder / "group-lists.txt", "folder": folder}
    world["journal"] = Journal()
    moves_agent(world)
    return world


def moves_drain(world):
    """The keeper of the lists finishes what it was nudged to do (and what that nudged)."""
    for _ in range(4):
        task = world["keeper"].task
        if task is None or task.done():
            return
        run(asyncio.wait({task}))


def moves_wanted(world):
    from awgscale.agent.adguard.profiles import fitting
    return fitting([UserGroupOf(record) for record in world["groups"].records], world["users"], world["own"].choices, world["whole"].names, world["dns"].routes).profiles or []


def moves_key(name, client):
    """What a client gives its people but their lists: everything the agent sends but its name and its tags."""
    from awgscale.agent.adguard.moves import as_profile, settings_of
    return None if name is None else settings_of(as_profile(name, client).model_copy(update={"tags": []}))


def moves_watch(world, broken):
    """From now until the next call of this: after every call to the blocker (its clients and its block lists), each
    person is filtered exactly as before the change or exactly as wanted — every setting but the lists; someone who was
    in no client and is wanted in none stays out. A whole-traffic person who was and stays one never asks other than
    the exit's resolvers. Their lists (what AdGuard's loaded lists block for them, adguard.group_lists): never without
    one they had and are wanted to have; never one that is neither what they had (or their client's tags gave them)
    nor what they are wanted to have, nor one brought to their client (the one they were in, are in, or go to) by the
    people going to it; and one who stays in their client never loses what they had before they have every list wanted
    (a children's group that switches lists keeps the old ones until the new ones block). (A newcomer of a client that
    is switching its lists may have, meanwhile, the old lists that client still carries, and everybody in a client
    waiting for a list the lists its newcomers had.) Breaches go to `broken`."""
    from awgscale.agent.adguard.group_lists import CONVERTED_LINE, sources_for
    from awgscale.agent.adguard.profiles import WHOLE_UPSTREAMS
    adguard = world["adguard"]
    want = {profile.name: profile for profile in moves_wanted(world)}
    final = {address: profile.name for profile in want.values() for address in profile.ids}
    addresses = {address.split("/")[0] for client in adguard.have.values() for address in client["ids"]} | set(final)

    def state(address):
        name = adguard.serving(address)
        client = adguard.have.get(name)
        return {"name": name, "key": moves_key(name, client), "lists": adguard.lists_of(address),
                "tags": sources_for((client or {}).get("tags") or []) if (client or {}).get("filtering_enabled") or (client or {}).get("use_global_settings") else frozenset()}

    before = {address: state(address) for address in addresses}
    tags_then = {name: sources_for(client.get("tags") or []) for name, client in adguard.have.items()}
    # the lists the people going to each client bring (the tags of the clients that hold them now): a client that waits
    # for a list may carry them meanwhile (adguard.profiles.kept_tags), and so may a stand-in copied from it
    held_by = {}
    for name, client in adguard.have.items():
        for identifier in client["ids"]:
            held_by.setdefault(identifier.split("/")[0], set()).add(name)
    incoming = {}
    for address, name in final.items():
        for holder in held_by.get(address, ()):
            incoming.setdefault(name, set()).update(tags_then[holder])
    after = {address: {"name": final[address], "key": moves_key(final[address], want[final[address]].wire()), "lists": sources_for(want[final[address]].tags)}
             if address in final else {"name": None, "key": None, "lists": frozenset()} for address in addresses}
    exit_dns = list(WHOLE_UPSTREAMS)

    def asks_exit(key):
        return key is not None and json.loads(key).get("upstreams", [])[:len(exit_dns)] == exit_dns

    def seen(call):
        for address in addresses:
            now = state(address)
            was = before[address]
            will = after[address]
            if asks_exit(was["key"]) and asks_exit(will["key"]) and not asks_exit(now["key"]):
                broken.append({"whole-traffic person off the exit's resolvers": address, "in": now["name"], "after": call, "calls": list(adguard.calls)})
            if now["key"] not in (was["key"], will["key"]):
                broken.append({"address": address, "was": was, "now": now, "wanted": will, "after": call, "calls": list(adguard.calls)})
                continue
            lacks = (was["lists"] & will["lists"]) - now["lists"]
            # a newcomer of a client that still carries its old tags (its lists are being switched) has them meanwhile
            third = now["lists"] - was["lists"] - was["tags"] - will["lists"] - tags_then.get(now["name"], frozenset())
            third -= set().union(*(incoming.get(name, set()) for name in (was["name"], now["name"], will["name"])))
            if CONVERTED_LINE in adguard.copies.get(str(world["file"]), ""):
                third = frozenset()     # converted from the older file of names: a name may carry more lists until they are downloaded again
            lost = was["name"] is not None and was["name"] == will["name"] and not will["lists"] <= now["lists"] and not was["lists"] <= now["lists"]
            if lacks or third or lost:
                broken.append({"lists of": address, "lacks": lacks, "third": third, "lost before the new ones": lost, "was": was, "now": now, "wanted": will,
                               "after": call, "calls": list(adguard.calls)[-6:], "list calls": list(adguard.list_calls)[-4:]})
    adguard.seen = seen


def moves_exact(world):
    """Does the blocker hold exactly the clients wanted, every one as wanted (its tags too), every person as their
    address; and has AdGuard loaded the file with exactly the lists needed?"""
    from awgscale.agent.adguard.group_lists import FileKind, head_of, wanted_sources
    from awgscale.agent.adguard.moves import same
    want = {profile.name: profile for profile in moves_wanted(world)}
    have = world["adguard"].have
    if set(have) != set(want) or not all(same(have[name], want[name].wire()) for name in want):
        return False
    needed = wanted_sources([UserGroupOf(record) for record in world["groups"].records], world["own"].choices)
    head = head_of(world["file"])
    if not needed:
        return head.kind == FileKind.MISSING and not world["adguard"].lists
    loaded = world["adguard"].copies.get(str(world["file"]))
    return head.kind == FileKind.TAGS and not head.converted and head.sources == needed and loaded == world["file"].read_text()


def moves_sync(world):
    return run(world["profiles"].sync([UserGroupOf(record) for record in world["groups"].records], world["users"]))


def moves_calm(world):
    """No call fails, every list downloads, the file can be written."""
    adguard = world["adguard"]
    adguard.failing = 0.0
    adguard.filters_failing = 0.0
    adguard.refuse_at = None
    adguard.lose_at = None
    world["downloads"].failing = set()
    world["downloads"].hanging = set()
    world["folder"].mkdir(parents=True, exist_ok=True)
    world["folder"].chmod(0o755)
    world["lists_memory"].failed_at = None           # the keeper waits a while after a failure: here it does not
    world["lists_memory"].failed_wanting = None


def moves_settles(world, broken):
    """Undisturbed changes (each a sync and what the keeper of the lists then does): within four the blocker is exactly
    as wanted, with exactly the lists needed loaded, and the next two make no call at all."""
    adguard = world["adguard"]
    moves_calm(world)
    quiet = 0
    for _ in range(6):
        moves_watch(world, broken)
        calls = len(adguard.calls) + len(adguard.list_calls)
        said = moves_sync(world)
        moves_drain(world)
        if said is None and moves_exact(world) and len(adguard.calls) + len(adguard.list_calls) == calls:
            quiet += 1
            if quiet == 2:
                return True
        else:
            quiet = 0
            moves_calm(world)
    return False


def moves_copy(world):
    """The same groups, choices, rules, blocker, file and memories, apart."""
    import copy
    moves_drain(world)
    twin = moves_world(copy.deepcopy(world["groups"].records), world["users"], dict(world["own"].choices), set(world["whole"].names), dict(world["dns"].routes))
    for field in ("have", "lists", "copies"):
        setattr(twin["adguard"], field, copy.deepcopy(getattr(world["adguard"], field)))
    twin["adguard"].lists = [dict(f, url=f["url"].replace(str(world["file"]), str(twin["file"]))) for f in twin["adguard"].lists]
    twin["adguard"].copies = {url.replace(str(world["file"]), str(twin["file"])): text for url, text in twin["adguard"].copies.items()}
    twin["adguard"].rnd = world["adguard"].rnd
    twin["folder"].mkdir(parents=True, exist_ok=True)
    for path in world["folder"].glob("group-lists.txt*"):
        shutil.copy(path, twin["folder"] / path.name)
    moves_agent(twin, copy.copy(world["lists_memory"]), copy.copy(world["profile_memory"]))
    return twin


def moves_every_stop(name, before, after, results, start=None):
    """A change from `before` to `after` made again and again, every one of its calls refused once, then answered and
    lost once (`start`, when given, first leaves the blocker as some earlier stopped change did): after every call
    everybody is filtered as before or as wanted, and the undisturbed changes that follow end exactly as wanted, then
    make no calls."""
    clean = before()
    moves_sync(clean)
    moves_drain(clean)
    if start:
        start(clean)
    clean["adguard"].calls.clear()
    after(clean)
    moves_sync(clean)
    total = len(clean["adguard"].calls)
    for number in range(total + 1):
        for how in ("refuse_at", "lose_at"):
            world = before()
            moves_sync(world)
            moves_drain(world)
            if start:
                start(world)
            world["adguard"].calls.clear()
            after(world)
            broken = []
            moves_watch(world, broken)
            setattr(world["adguard"], how, number)
            moves_sync(world)
            setattr(world["adguard"], how, None)
            settled = moves_settles(world, broken)
            broken += [{"a call AdGuard refuses": invalid} for invalid in world["adguard"].invalid]
            results.append((name, number, how, broken, settled))
    return total


MOVES_FILTERS = ["std", "off", "kids", "pro", "proplus", "ultimate"]
MOVES_GROUPS = ["aaaaa1", "bbbbb1", "ccccc1", "ddddd1", "eeeee1"]
MOVES_KIDS_LISTS = ["nsfw", "gambling", "bypass", "social"]
MOVES_SOURCES = ["pro", "pro.plus", "ultimate", "nsfw", "gambling.mini", "doh-vpn-proxy-bypass", "social"]


def moves_route(rnd, group):
    from awgscale.agent.sites.scoped import RouteDns
    return RouteDns(upstreams=("8.8.8.8", f"[/{group}.example/]1.1.1.{rnd.randint(1, 2)}"), direct_lines=("[/d.example/]9.9.9.9",))


def moves_random(world, rnd, people, small=False, lists=True):
    """A random state of the groups, choices, whole-traffic people and rules — the review's fuzz (r4/fuzz.py) draw for
    draw; `small`: one change of it. `lists`: also the children's lists of the groups (so that clients some list
    names are moved too), and now and then two groups of their own clients ("kids", "off") that swap their people and
    their filters (two clients whose settings change and that wait for each other: a stand-in)."""
    records = world["groups"].records
    if small and records:
        draw = rnd.random()
        if draw < 0.35:
            person = rnd.choice(people).name
            for record in records:
                record["members"] = [member for member in record["members"] if member != person]
            record = rnd.choice(records + [None])
            if record:
                record["members"] = record["members"] + [person]
        elif draw < 0.6:
            rnd.choice(records)["filter"] = rnd.choice(MOVES_FILTERS)
        elif draw < 0.8:
            group = rnd.choice(MOVES_GROUPS)
            if group in world["dns"].routes:
                del world["dns"].routes[group]
            else:
                world["dns"].routes[group] = moves_route(rnd, group)
        elif draw < 0.9:
            person = rnd.choice(people).name
            if person in world["own"].choices and rnd.random() < 0.5:
                del world["own"].choices[person]
            else:
                world["own"].choices[person] = rnd.choice(MOVES_FILTERS)
        else:
            world["whole"].names ^= {rnd.choice(people).name}
        if lists:
            for record in records:
                if rnd.random() < 0.1:
                    record["kids_lists"] = rnd.sample(MOVES_KIDS_LISTS, rnd.randint(0, 2))
            if rnd.random() < 0.5:
                first, second = rnd.sample(records, 2)
                members = first["members"]
                first["members"] = second["members"]
                second["members"] = members
                kinds = rnd.sample(["kids", "off"], 2)
                first["filter"] = kinds[0]
                second["filter"] = kinds[1]
                for group in (first["id"], second["id"]):
                    if group in world["dns"].routes:
                        world["dns"].routes[group] = moves_route(rnd, group)
        return
    homes = {person.name: rnd.choice([None, *MOVES_GROUPS]) for person in people}
    world["groups"].records = [{"id": group, "name": group, "filter": rnd.choice(MOVES_FILTERS), "members": [name for name, home in homes.items() if home == group]}
                               for group in MOVES_GROUPS]
    world["own"].choices = {person.name: rnd.choice(MOVES_FILTERS) for person in people if rnd.random() < 0.3}
    world["whole"].names = {person.name for person in people if rnd.random() < 0.25}
    world["dns"].routes = {group: moves_route(rnd, group) for group in MOVES_GROUPS if rnd.random() < 0.4}
    if lists:
        for record in world["groups"].records:
            record["kids_lists"] = rnd.sample(MOVES_KIDS_LISTS, rnd.randint(0, 2))


def moves_older_file(world):
    """The agent of ddd7ca8 left its file of names and untagged clients: what it named each client with is what the
    clients' tags say now (the lists it gave them); AdGuard has loaded that file."""
    from awgscale.agent.adguard.group_lists import GroupSource, sources_for
    adguard = world["adguard"]
    named = {name: sources_for(client.get("tags") or []) for name, client in sorted(adguard.have.items())}
    named = {name: sources for name, sources in named.items() if sources}
    lines = ["! Title: Списки групп", "! Needs: " + " ".join(f"{name}={','.join(s.value for s in GroupSource if s in sources)}" for name, sources in named.items())]
    by_name = {}
    for name, sources in named.items():
        for source in sources:
            for found in re.finditer(r"^\|\|(.+)\^$", list_text(source), re.MULTILINE):
                by_name.setdefault(found.group(1), set()).add(name)
    lines += [f"||{rule}^$client=" + "|".join(f"'{name}'" for name in sorted(names)) for rule, names in sorted(by_name.items())]
    for client in adguard.have.values():
        client["tags"] = []
    world["folder"].mkdir(parents=True, exist_ok=True)
    world["file"].write_text("\n".join(lines) + "\n")
    adguard.lists = [{"url": str(world["file"]), "name": "Списки групп", "enabled": True}] if named else []
    adguard.copies = {str(world["file"]): world["file"].read_text()} if named else {}
    moves_agent(world)


class MovesCount:
    """What a fuzz went through: changes, calls, temporary clients made, starts left dirty by a stop, and on the lists'
    side: downloads that failed or hung, calls of the block lists that failed, writes refused, agents started again,
    older files of names replaced."""

    def __init__(self):
        self.changes = 0
        self.calls = 0
        self.temporaries = 0
        self.dirty = 0
        self.list_failures = 0
        self.restarts = 0
        self.upgrades = 0
        self.broken = []
        self.unsettled = []

    def took(self, adguard, since):
        from awgscale.agent.adguard.moves import MOVE_PROFILE
        self.calls += len(adguard.calls) - since
        self.temporaries += sum(1 for call in adguard.calls[since:] if call[0] == "add" and call[1].startswith(MOVE_PROFILE))

    def looked_at(self, adguard):
        from awgscale.agent.adguard.moves import MOVE_PROFILE
        self.dirty += any(name.startswith(MOVE_PROFILE) or any("/" in identifier for identifier in client["ids"]) for name, client in adguard.have.items())

    def invalid(self, adguard):
        self.broken += [{"a call AdGuard refuses": invalid} for invalid in adguard.invalid]
        adguard.invalid.clear()


def moves_trouble(world, rnd, count):
    """The lists' side goes wrong at random for one change: downloads fail or hang, the calls of the block lists fail,
    the file cannot be written, the agent is started again; the keeper is waited for, or not."""
    from awgscale.agent.adguard.group_lists import GroupSource
    draw = rnd.random()
    downloads = world["downloads"]
    downloads.failing = {GroupSource(value) for value in MOVES_SOURCES if rnd.random() < 0.15} if draw < 0.5 else set()
    downloads.hanging = {GroupSource(value) for value in MOVES_SOURCES if rnd.random() < 0.1} if draw < 0.3 else set()
    world["adguard"].filters_failing = rnd.choice([0.0, 0.0, 0.2, 0.5])
    world["folder"].mkdir(parents=True, exist_ok=True)
    world["folder"].chmod(0o555 if rnd.random() < 0.1 else 0o755)
    count.list_failures += bool(downloads.failing or downloads.hanging) + bool(world["adguard"].filters_failing) + (world["folder"].stat().st_mode & 0o200 == 0)
    if rnd.random() < 0.15:
        count.restarts += 1
        moves_agent(world)


def moves_fuzz(seed, trials, people, lists=True):
    """The review's fuzz (r4/fuzz.py) as a check: chains of random changes, each stopped at a random call or failing
    at random, sometimes followed at once by a small change; after every call the invariant, and in the end the
    undisturbed changes settle. With `lists` off it draws exactly as the review's script did; with it, the lists' side
    goes wrong too (moves_trouble), and a chain may start from the older agent's file of names. → MovesCount."""
    import random
    rnd = random.Random(seed)
    count = MovesCount()
    for trial in range(trials):
        world = moves_world([], people)
        adguard = world["adguard"]
        adguard.rnd = rnd
        moves_random(world, rnd, people, lists=lists)
        moves_sync(world)
        moves_drain(world)
        if lists and rnd.random() < 0.3:
            moves_settles(world, count.broken)
            moves_older_file(world)
            count.upgrades += 1
        for _ in range(rnd.randint(2, 6)):
            moves_random(world, rnd, people, small=rnd.random() < 0.6, lists=lists)
            mode = rnd.random()
            adguard.refuse_at = None
            adguard.lose_at = None
            adguard.failing = 0.0
            if mode < 0.4:
                setattr(adguard, rnd.choice(["refuse_at", "lose_at"]), len(adguard.calls) + rnd.randint(0, 6))
            elif mode < 0.7:
                adguard.failing = rnd.choice([0.1, 0.3])
            moves_watch(world, count.broken)
            if lists:
                moves_trouble(world, rnd, count)
            before = len(adguard.calls)
            moves_sync(world)
            if lists and rnd.random() < 0.5:
                moves_drain(world)
            count.took(adguard, before)
            count.changes += 1
            adguard.refuse_at = None
            adguard.lose_at = None
            adguard.failing = 0.0
            count.looked_at(adguard)
            if rnd.random() < 0.5:
                moves_random(world, rnd, people, small=True, lists=lists)
                before = len(adguard.calls)
                settled = moves_settles(world, count.broken)
                count.took(adguard, before)
                if not settled:
                    count.unsettled.append((seed, trial, json.loads(json.dumps(adguard.have))))
                    break
        before = len(adguard.calls)
        if not moves_settles(world, count.broken):
            count.unsettled.append((seed, trial, json.loads(json.dumps(adguard.have))))
        count.took(adguard, before)
        count.invalid(adguard)
    return count


def moves_dirty_stops(seed, pairs, people):
    """From dirty starts: a random change stopped at a random call (refused, or answered and lost), then another
    change stopped at every one of its calls in turn, both ways; after every call the invariant, then the undisturbed
    changes settle. → (stop positions, MovesCount)."""
    import random
    rnd = random.Random(seed)
    positions = 0
    count = MovesCount()
    for _ in range(pairs):
        origin = moves_world([], people)
        origin["adguard"].rnd = rnd
        moves_random(origin, rnd, people)
        moves_sync(origin)
        moves_drain(origin)
        moves_random(origin, rnd, people, small=rnd.random() < 0.5)
        setattr(origin["adguard"], rnd.choice(["refuse_at", "lose_at"]), len(origin["adguard"].calls) + rnd.randint(0, 8))
        moves_sync(origin)
        origin["adguard"].refuse_at = None
        origin["adguard"].lose_at = None
        count.looked_at(origin["adguard"])
        moves_random(origin, rnd, people, small=rnd.random() < 0.5)
        probe = moves_copy(origin)
        probe["adguard"].calls.clear()
        moves_sync(probe)
        for number in range(len(probe["adguard"].calls) + 1):
            for how in ("refuse_at", "lose_at"):
                positions += 1
                world = moves_copy(origin)
                world["adguard"].calls.clear()
                moves_watch(world, count.broken)
                setattr(world["adguard"], how, number)
                moves_sync(world)
                if not moves_settles(world, count.broken):
                    count.unsettled.append((json.loads(json.dumps(origin["adguard"].have)), how, number, world["adguard"].calls))
                count.took(world["adguard"], 0)
                count.invalid(world["adguard"])
        count.invalid(origin["adguard"])
    return positions, count


def moves_cycles(seed, pairs, people):
    """Clients that wait for each other: two groups with clients of their own ("kids", "off") swap their people and
    their filters, from a start sometimes left dirty by a stopped change, with whole-traffic people, rules and
    children's lists at random; every call of the swap stopped in turn, both ways. → (stop positions, MovesCount)."""
    import random
    rnd = random.Random(seed)
    positions = 0
    count = MovesCount()
    for _ in range(pairs):
        kinds = rnd.sample(["kids", "off"], 2)
        homes = {person.name: rnd.choice(["aaaaa1", "bbbbb1", None]) for person in people}
        records = [{"id": group, "name": group, "filter": kind, "members": [name for name, home in homes.items() if home == group],
                    "kids_lists": rnd.sample(MOVES_KIDS_LISTS, rnd.randint(0, 2))} for group, kind in zip(["aaaaa1", "bbbbb1"], kinds)]
        origin = moves_world(records, people, whole={person.name for person in people if rnd.random() < 0.2})
        origin["adguard"].rnd = rnd
        origin["dns"].routes = {group: moves_route(rnd, group) for group in ("aaaaa1", "bbbbb1") if rnd.random() < 0.4}
        moves_sync(origin)
        moves_drain(origin)
        if rnd.random() < 0.5:
            moves_random(origin, rnd, people, small=True, lists=False)
            setattr(origin["adguard"], rnd.choice(["refuse_at", "lose_at"]), len(origin["adguard"].calls) + rnd.randint(0, 4))
            moves_sync(origin)
            origin["adguard"].refuse_at = None
            origin["adguard"].lose_at = None
        count.looked_at(origin["adguard"])
        first = origin["groups"].records[0]
        second = origin["groups"].records[1]
        members = first["members"]
        first["members"] = second["members"]
        second["members"] = members
        kinds = [first["filter"], second["filter"]]
        first["filter"] = kinds[1]
        second["filter"] = kinds[0]
        probe = moves_copy(origin)
        probe["adguard"].calls.clear()
        moves_sync(probe)
        for number in range(len(probe["adguard"].calls) + 1):
            for how in ("refuse_at", "lose_at"):
                positions += 1
                world = moves_copy(origin)
                world["adguard"].calls.clear()
                moves_watch(world, count.broken)
                setattr(world["adguard"], how, number)
                moves_sync(world)
                if not moves_settles(world, count.broken):
                    count.unsettled.append((json.loads(json.dumps(origin["adguard"].have)), how, number, world["adguard"].calls))
                count.took(world["adguard"], 0)
                count.invalid(world["adguard"])
    return positions, count


def t_client_moves():
    print("\n— клиенты блокировщика меняются, а человек всё время отфильтрован как был или как надо (отказы, потерянные ответы, обрывы)")
    from awgscale.agent.adguard.moves import MOVE_PROFILE, ClientMoves, host_id
    from awgscale.agent.adguard.models import Profile
    from awgscale.agent.sites.scoped import RouteDns
    bob = KnownUser(name="bob", ip="10.9.9.3")
    ann = KnownUser(name="ann", ip="10.9.9.5")
    eve = KnownUser(name="eve", ip="10.9.9.7")
    kid = KnownUser(name="kid", ip="10.9.9.9")
    route_a = {"aaaaa1": RouteDns(upstreams=("8.8.8.8", "[/a.example/]1.1.1.1"), direct_lines=())}
    route_b = {"bbbbb1": RouteDns(upstreams=("8.8.8.8", "[/a.example/]1.1.1.1"), direct_lines=())}

    def two_groups():
        return moves_world([{"id": "aaaaa1", "name": "A", "filter": "std", "members": ["bob"]}, {"id": "bbbbb1", "name": "B", "filter": "std", "members": ["ann"]}],
                           [bob, ann], own={"bob": "kids", "ann": "kids"})

    def addfail_after(world):
        world["dns"].routes = dict(route_a)

    def move_before():
        world = two_groups()
        world["dns"].routes = dict(route_a)
        return world

    def move_after(world):
        world["dns"].routes = dict(route_b)

    def swap_before():
        return moves_world([{"id": "aaaaa1", "name": "A", "filter": "kids", "members": ["bob"]}, {"id": "bbbbb1", "name": "B", "filter": "pro", "members": ["ann"]}], [bob, ann])

    def swap_after(world):
        world["groups"].records = [dict(world["groups"].records[0], members=["ann"]), dict(world["groups"].records[1], members=["bob"])]

    def flip_before():
        return moves_world([{"id": "aaaaa1", "name": "A", "filter": "kids", "members": ["bob", "ann"]}, {"id": "bbbbb1", "name": "B", "filter": "off", "members": []}], [bob, ann])

    def flip_after(world):
        world["groups"].records = [dict(world["groups"].records[0], filter="off", members=["bob"]), dict(world["groups"].records[1], filter="kids", members=["ann"])]

    def cycle_before():
        return moves_world([{"id": "aaaaa1", "name": "A", "filter": "off", "members": ["bob", "eve"]}, {"id": "bbbbb1", "name": "B", "filter": "kids", "members": ["ann", "kid"]}],
                           [bob, ann, eve, kid])

    def cycle_after(world):
        world["groups"].records = [dict(world["groups"].records[0], filter="kids", members=["bob", "ann"]), dict(world["groups"].records[1], filter="off", members=["eve", "kid"])]

    def listed_before():
        return moves_world([{"id": "aaaaa1", "name": "A", "filter": "pro", "members": ["bob", "eve"]}, {"id": "bbbbb1", "name": "B", "filter": "pro", "members": ["ann", "kid"]}],
                           [bob, ann, eve, kid], routes={**route_a, **route_b})

    def listed_after(world):
        world["groups"].records = [dict(world["groups"].records[0], members=["bob", "ann"]), dict(world["groups"].records[1], members=["eve", "kid"])]
        world["dns"].routes = {group: RouteDns(upstreams=("8.8.8.8", f"[/{group}.example/]1.1.1.2"), direct_lines=()) for group in ("aaaaa1", "bbbbb1")}

    def stall_before():
        """r4/stall_sync.py: Bob goes from A (kids) to B (pro), and the release is refused…"""
        return moves_world([{"id": "aaaaa1", "name": "A", "filter": "kids", "members": ["bob", "ann"]}, {"id": "bbbbb1", "name": "B", "filter": "pro", "members": ["eve"]},
                            {"id": "ccccc1", "name": "C", "filter": "kids", "members": []}], [bob, ann, eve, kid])

    def stall_start(world):
        world["groups"].records[0]["members"] = ["ann"]
        world["groups"].records[1]["members"] = ["eve", "bob"]
        world["adguard"].refuse_at = len(world["adguard"].calls) + 1
        moves_sync(world)
        world["adguard"].refuse_at = None

    def stall_after(world):
        """…then A's filter is switched off and a child joins the children's group C."""
        world["groups"].records[0]["filter"] = "off"
        world["groups"].records[2]["members"] = ["kid"]

    results = []
    cases = (("addfail", two_groups, addfail_after, None), ("move", move_before, move_after, None), ("swap", swap_before, swap_after, None),
             ("flip", flip_before, flip_after, None), ("cycle", cycle_before, cycle_after, None), ("listed cycle", listed_before, listed_after, None),
             ("r4/stall_sync", stall_before, stall_after, stall_start))
    for name, before, after, start in cases:
        total = moves_every_stop(name, before, after, results, start)
        mine = [result for result in results if result[0] == name]
        ok(total > 0 and all(not result[3] for result in mine) and all(result[4] for result in mine),
           f"{name}: каждый из {total} вызовов отказан, затем выполнен с потерянным ответом — после каждого вызова каждый отфильтрован ровно как был или как надо "
           "(все настройки; списки — метки клиента, временный клиент несёт их тоже); спокойные разы — ровно как надо, потом ни одного вызова",
           [result for result in mine if result[3] or not result[4]][:1])

    world = stall_before()
    moves_sync(world)
    stall_start(world)
    stall_after(world)
    said = [moves_sync(world) for _ in range(3)]
    ok(said == [None, None, None] and moves_exact(world) and world["adguard"].serving(kid.ip) == "grp-ccccc1",
       "r4/stall_sync.py: после отказа и новой правки следующий раз проходит — «kid» в детском клиенте C, ничего не отказывается раз за разом", [said, world["adguard"].have])
    world["groups"].records[0]["filter"] = "kids"
    ok(moves_sync(world) is None and moves_exact(world) and world["adguard"].serving(kid.ip) == "grp-ccccc1", "r4/recover.py: и фильтр A, возвращённый назад, тоже проходит", world["adguard"].have)

    def literal(name, ids, kids):
        return Profile(name=name, ids=ids, filtering_enabled=True, parental_enabled=kids, safebrowsing_enabled=True, safe_search={"enabled": kids})

    def direct(name, have, want, limit=None):
        """ClientMoves straight on a blocker left as `have`: every call refused once, then answered and lost once; the
        invariant after every call; then the undisturbed runs end exactly as wanted and make no calls."""
        from awgscale.agent.adguard.moves import MOVE_REQUEST_BYTES, as_profile, settings_of
        limit = limit or MOVE_REQUEST_BYTES
        wanted_at = {address: profile for profile in want for address in profile.ids}
        broken = []
        settled = []

        def moves(adguard):
            return run(ClientMoves(adguard, want, json.loads(json.dumps(adguard.have)), limit=limit).run())
        probe = StrictAdGuard(limit=limit)
        probe.have = json.loads(json.dumps(have))
        moves(probe)
        total = len(probe.calls)
        for number in range(total + 1):
            for how in ("refuse_at", "lose_at"):
                adguard = StrictAdGuard(limit=limit)
                adguard.have = json.loads(json.dumps(have))
                setattr(adguard, how, number)
                addresses = {identifier.split("/")[0] for client in have.values() for identifier in client["ids"]} | set(wanted_at)

                def key(address, adguard=adguard):
                    holder = adguard.serving(address)
                    return None if holder is None else settings_of(as_profile(holder, adguard.have[holder]))
                was = {address: key(address) for address in addresses}

                def seen(call, adguard=adguard, was=was, key=key):
                    for address in addresses:
                        wanted = wanted_at.get(address)
                        now = key(address)
                        if now not in (was[address], None if wanted is None else settings_of(wanted)):
                            broken.append((address, was[address], key(address), list(adguard.calls)))
                adguard.seen = seen
                moves(adguard)
                setattr(adguard, how, None)
                adguard.seen = None
                outcomes = []
                for _ in range(3):
                    calls = len(adguard.calls)
                    outcomes.append(moves(adguard) == [] and len(adguard.calls) - calls)
                exact = set(adguard.have) == {profile.name for profile in want} and all(sorted(adguard.have[profile.name]["ids"]) == sorted(profile.ids) for profile in want)
                settled.append(exact and outcomes[1:] == [0, 0] and outcomes[0] is not False)
                broken += [("a call AdGuard refuses", invalid) for invalid in adguard.invalid]
        ok(not broken and all(settled), f"{name}: каждый из {total} вызовов отказан, затем без ответа — каждый как был или как надо; потом ровно как надо, затем ни одного вызова",
           [broken[:1], settled])
        return total

    have = {"grp-own-kids": literal("grp-own-kids", ["10.0.0.2"], True).wire(), "grp-old": literal("grp-old", ["10.0.0.1"], False).wire()}
    direct("swap ревью r2 (детский «grp-own-kids» берёт адрес у недетского ненужного клиента, а свой отдаёт новому)", have,
           [literal("grp-own-kids", ["10.0.0.1"], True), literal("grp-new", ["10.0.0.2"], True)])
    direct("r4/stall.py (Y уже держит /32 человека из X, а у X меняется фильтрация)",
           {"grp-x": literal("grp-x", ["10.0.0.1", "10.0.0.2"], False).wire(), "grp-y": literal("grp-y", ["10.0.0.3", "10.0.0.1/32"], False).wire()},
           [literal("grp-x", ["10.0.0.2"], True), literal("grp-y", ["10.0.0.1", "10.0.0.3"], False)])
    direct("r4/stall2.py (нужные клиенты держат только устаревшие /32, каждый — того, кто нужен другому)",
           {"grp-p": literal("grp-p", ["10.0.0.9/32"], False).wire(), "grp-q": literal("grp-q", ["10.0.0.8/32"], True).wire(), "grp-z": literal("grp-z", ["10.0.0.8", "10.0.0.9"], False).wire()},
           [literal("grp-p", ["10.0.0.8"], False), literal("grp-q", ["10.0.0.9"], True)])

    long_upstreams = ["8.8.8.8", *(f"[/site{number}.example.net/]1.1.1.1" for number in range(40))]
    crowd = [f"10.7.{number // 200}.{number % 200 + 2}" for number in range(240)]

    def routed(name, ids):
        return literal(name, ids, False).model_copy(update={"upstreams": long_upstreams, "upstreams_cache_enabled": True, "upstreams_cache_size": 1 << 20})
    from awgscale.agent.adguard.moves import request_bytes
    start = [routed("grp-x-rxxxxx1", crowd[:120]), routed("grp-y-ryyyyy1", crowd[120:])]
    have = {profile.name: profile.wire() for profile in start}
    want = [routed("grp-x-rxxxxx1", crowd[60:180]), routed("grp-y-ryyyyy1", crowd[:60] + crowd[180:])]
    small_limit = max(request_bytes(profile) for profile in start + want) + 40
    sizes = StrictAdGuard(limit=small_limit)
    sizes.have = json.loads(json.dumps(have))
    said = run(ClientMoves(sizes, want, json.loads(json.dumps(have)), limit=small_limit).run())
    biggest = max(request_bytes(Profile(**(call[3] if call[0] == "update" else call[2]))) for call in sizes.sized)
    ok(said == [] and biggest <= small_limit and set(sizes.have) == {"grp-x-rxxxxx1", "grp-y-ryyyyy1"} and sorted(sizes.have["grp-x-rxxxxx1"]["ids"]) == sorted(crowd[60:180]),
       f"два полных клиента меняются половиной людей, а запрос в середине переезда ограничен ({small_limit} байт — самый большой из клиентов плюс 40): переезд по частям, ни один запрос не больше",
       [said, biggest, len(sizes.calls)])
    direct("то же, каждый вызов отказан и без ответа", have, want, small_limit)

    world = two_groups()
    moves_sync(world)
    world["adguard"].calls.clear()
    world["dns"].routes = dict(route_a)
    moves_sync(world)
    calls = world["adguard"].calls
    ok(calls[0][0] == "add" and calls[0][2] == [host_id("10.9.9.3")] and calls[1][0] == "update" and calls[1][1] == "grp-own-kids" and calls[1][3] == ["10.9.9.5"],
       "addfail ревью: новый вариант сначала берёт Боба как сеть /32, и только потом «grp-own-kids» его отпускает", calls)
    world = cycle_before()
    moves_sync(world)
    world["adguard"].calls.clear()
    cycle_after(world)
    moves_sync(world)
    calls = world["adguard"].calls
    ok(any(call[0] == "add" and call[1].startswith(MOVE_PROFILE) for call in calls) and moves_exact(world),
       "у двух групп меняется фильтр, и они меняются людьми: уходящих из одной держит временный клиент с её прежними настройками (его не называет ни один список)", calls)
    world = flip_before()
    moves_sync(world)
    world["adguard"].calls.clear()
    flip_after(world)
    moves_sync(world)
    calls = world["adguard"].calls
    ok(not any(call[0] == "add" and call[1].startswith(MOVE_PROFILE) for call in calls) and moves_exact(world),
       "фильтр группы меняется, а часть её людей уходит в готовый клиент: сначала тот их берёт, потом группа меняется и отпускает их одним вызовом — без временного клиента", calls)

    import importlib.util
    from awgscale.agent.adguard.profiles import wanted
    spec = importlib.util.spec_from_file_location("stack_adguard", os.path.join(HERE, "stack_adguard.py"))
    stack_adguard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stack_adguard)
    made = {profile.name: profile.wire() for profile in wanted([UserGroupOf({"id": "kkkkk1", "name": "K", "filter": "kids", "members": ["bob", "ann"]}),
                                                                 UserGroupOf({"id": "ppppp1", "name": "P", "filter": "pro", "members": ["eve"]})],
                                                                [KnownUser(name="bob", ip="10.9.0.6"), KnownUser(name="ann", ip="10.9.0.7"), KnownUser(name="eve", ip="10.9.0.8"),
                                                                 KnownUser(name="dan", ip="10.9.0.10")],
                                                                whole={"eve", "dan"})}
    sent = {client["name"]: client for client in stack_adguard.AGENT_CLIENTS}
    ok(set(sent) == {"grp-kkkkk1", "grp-pro-all", "grp-all"} and set(sent) <= set(made) and all({**made[name], "upstreams": None} == {**client, "upstreams": None} for name, client in sent.items()),
       "tests/stack_adguard.py читает назад клиентов ровно таких, какими их шлёт агент (детский, «-all» уровня и «grp-all» с общими настройками; кроме самих адресов резолверов)", [made, sent])
    from awgscale.agent.adguard.group_lists import GroupSource, ListTag, bit_of, file_lines
    made_file = "".join(file_lines({"track.test": bit_of(GroupSource.PRO) | bit_of(GroupSource.PROPLUS) | bit_of(GroupSource.NSFW), "adult.test": bit_of(GroupSource.NSFW),
                                    "plus.test": bit_of(GroupSource.PROPLUS)}))
    ok(stack_adguard.LIST_TAGS == [tag.value for tag in ListTag] and stack_adguard.AGENT_FILE == made_file,
       "tests/stack_adguard.py проверяет на AdGuard ровно метки списков агента и файл ровно такой, какой агент пишет", [stack_adguard.LIST_TAGS, made_file])

    world = moves_world([{"id": "aaaaa1", "name": "A", "filter": "kids", "members": ["bob"]}, {"id": "bbbbb1", "name": "B", "filter": "std", "members": ["ann"]}],
                        [bob, ann, KnownUser(name="eve", ip="10.9.9.7")], routes=route_b)
    world["dns"].broken = True
    first = moves_sync(world)
    ok(first and "правила для групп не прочитаны" in first and not world["adguard"].calls,
       "правила для групп ни разу не прочитаны (агент только что запущен): ничего не меняется — догадка вынула бы людей из вариантов клиентов", first)
    world["dns"].broken = False
    moves_sync(world)
    variant = [name for name in world["adguard"].have if name.startswith("grp-rbbbbb1")]
    world["dns"].broken = True
    world["groups"].records[0]["members"] = ["bob", "eve"]
    said = len(world["journal"].lines)
    stalled = moves_sync(world)
    moves_sync(world)
    ok(stalled is None and world["adguard"].have["grp-aaaaa1"]["ids"] == ["10.9.9.3", "10.9.9.7"] and variant and all(name in world["adguard"].have for name in variant)
       and len(world["journal"].lines) == said + 1 and world["journal"].lines[-1]["bad"],
       "правила для групп не читаются: обычные детские клиенты всё равно меняются (Ева — в «Дети»), правила — как прочитаны в последний раз, в журнале — один раз",
       [stalled, sorted(world["adguard"].have), world["journal"].lines[said:]])
    world["dns"].broken = False
    moves_sync(world)
    ok(len(world["journal"].lines) == said + 2 and not world["journal"].lines[-1]["bad"], "правила снова читаются — в журнале одна строка об этом", world["journal"].lines[said:])

    people = [KnownUser(name=f"p{number}", ip=f"10.9.8.{10 + number}") for number in range(8)]
    fuzz_trials = int(os.environ.get("MOVES_FUZZ_TRIALS", "320"))
    for seed, lists in ((3, False), (11, False), (12, False), (21, True), (22, True)):
        count = moves_fuzz(seed, fuzz_trials, people, lists)
        how = "с детскими списками и обменом людьми между группами, у которых меняется фильтр" if lists else "розыгрыш ровно как в r4/fuzz.py"
        ok(not count.broken and not count.unsettled,
           f"fuzz, seed {seed} ({how}): {fuzz_trials} цепочек, {count.changes} изменений, {count.calls} вызовов, {count.dirty} раз блокировщик оставлен с /32 "
           f"или временным клиентом, {count.temporaries} временных клиентов: каждый всё время как был или как надо, ни одного вызова, который блокировщик отверг бы сам; "
           f"три спокойных раза — ровно как надо, потом ни одного вызова (сбоев на стороне списков: {count.list_failures}, перезапусков агента: {count.restarts}, файлов прежнего агента: {count.upgrades})",
           [count.broken[:1], count.unsettled[:1]])
    pairs = int(os.environ.get("MOVES_DIRTY_PAIRS", "120"))
    positions, count = moves_cycles(20261124, pairs, people)
    ok(not count.broken and not count.unsettled and count.temporaries,
       f"две группы со своими клиентами меняются людьми и фильтрами ({pairs} случаев, {count.dirty} с грязного старта): остановка на каждом из вызовов — "
       f"{positions} положений, {count.calls} вызовов, {count.temporaries} временных клиентов: каждый всё время как был или как надо; затем ровно как надо и ни "
       f"одного вызова (сбоев на стороне списков: {count.list_failures}, перезапусков агента: {count.restarts}, файлов прежнего агента: {count.upgrades})", [count.broken[:1], count.unsettled[:1]])
    positions, count = moves_dirty_stops(20261123, pairs, people)
    ok(not count.broken and not count.unsettled,
       f"с грязного старта (прошлое изменение остановлено на случайном вызове; {count.dirty} из {pairs} стартов с /32 или временным клиентом): остановка на каждом "
       f"из вызовов следующего изменения — {positions} положений, {count.calls} вызовов, {count.temporaries} временных клиентов: каждый всё время как был или как надо; "
       f"затем ровно как надо и ни одного вызова (сбоев на стороне списков: {count.list_failures}, перезапусков агента: {count.restarts}, файлов прежнего агента: {count.upgrades})", [count.broken[:1], count.unsettled[:1]])


OLDER_LISTS = r"""
import asyncio, copy, json, sys
sys.path.insert(0, sys.argv[1])
import awgscale
assert awgscale.__file__.startswith(sys.argv[1]), awgscale.__file__
from pathlib import Path
from awgscale.agent.adguard.group_lists import GroupBlockLists, GroupListsMemory
from awgscale.agent.adguard.models import FilterList
from awgscale.agent.adguard.profiles import AdGuardProfiles, ProfileMemory
from awgscale.agent.ports import Outcome
from awgscale.agent.users.models import KnownUser, StoredGroups, UserGroup

state = json.loads(Path(sys.argv[2]).read_text())
snapshots = []


class AdGuard:
    def __init__(self):
        self.have = state["have"]
        self.lists = state["lists"]
        self.copies = state["copies"]
        self.calls = []

    def taken(self):
        snapshots.append({"have": copy.deepcopy(self.have), "lists": copy.deepcopy(self.lists), "copies": dict(self.copies), "call": self.calls[-1]})

    async def held(self):
        return copy.deepcopy(self.have)

    async def add(self, profile):
        self.calls.append(["add", profile.name])
        if profile.name in self.have or any(set(profile.ids) & set(c["ids"]) for c in self.have.values()):
            self.taken()
            return Outcome(False, "taken")
        self.have[profile.name] = dict(profile.wire(), tags=sorted(profile.wire().get("tags") or []))
        self.taken()
        return Outcome(True)

    async def delete(self, name):
        self.calls.append(["delete", name])
        self.have.pop(name, None)
        self.taken()
        return Outcome(True)

    async def filters(self):
        return [FilterList(id=i, url=f["url"], name=f["name"], enabled=f["enabled"]) for i, f in enumerate(self.lists)]

    async def add_filter(self, name, url):
        self.calls.append(["add_url", url])
        self.lists.append({"url": url, "name": name, "enabled": True})
        self.copies[url] = Path(url).read_text()
        self.taken()

    async def set_filter(self, filter_list, name, enabled, timeout=None):
        self.calls.append(["set_url", filter_list.url])
        for f in self.lists:
            if f["url"] == filter_list.url:
                f["enabled"] = enabled
        self.taken()

    async def remove_filter(self, filter_list):
        self.calls.append(["remove_url", filter_list.url])
        self.lists = [f for f in self.lists if f["url"] != filter_list.url]
        self.taken()

    async def refresh(self):
        self.calls.append(["refresh"])
        for f in self.lists:
            if f["enabled"] and f["url"].startswith("/"):
                self.copies[f["url"]] = Path(f["url"]).read_text()
        self.taken()


class Groups:
    async def read(self):
        return StoredGroups.of(copy.deepcopy(state["groups"]))


class Own:
    async def read(self):
        return dict(state["own"])


class Whole:
    async def whole(self):
        return set(state["whole"])


class Journal:
    async def write(self, *a, **k):
        pass


class Downloads:
    async def text(self, source):
        return state["texts"][source.value]


async def main():
    adguard = AdGuard()
    keeper = GroupBlockLists(adguard, Groups(), Journal(), Downloads(), Path(state["file"]), 1, GroupListsMemory(), own=Own())
    profiles = AdGuardProfiles(adguard, Groups(), Journal(), Path(state["file"] + ".lock"), ProfileMemory(), group_lists=keeper, own=Own(), routes=Whole())
    users = [KnownUser(name=u["name"], ip=u["ip"]) for u in state["users"]]
    said = await profiles.sync([UserGroup.of(g) for g in state["groups"]], users)
    for _ in range(50):
        await asyncio.sleep(0.01)
    said_lists = await keeper.keep()
    state.update(have=adguard.have, lists=adguard.lists, copies=adguard.copies)
    print(json.dumps({"said": said, "said_lists": said_lists, "calls": adguard.calls, "snapshots": snapshots, "state": state}))


asyncio.run(main())
"""


def older_agent(world):
    """ddd7ca8 — the agent before the lists by tags — put back on this world's AdGuard and file: its sync of the
    clients and its keeper of the lists, once each. → (what it said, every state of AdGuard after each of its calls);
    the world's AdGuard is left as it left it."""
    import subprocess
    from awgscale.agent.adguard.group_lists import GroupSource
    older = place("ddd7ca8")
    if not (older / "src").exists():
        older.mkdir(exist_ok=True)
        archive = subprocess.run(["git", "-C", ROOT, "archive", "ddd7ca8", "src/awgscale"], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(older)], input=archive.stdout, check=True)
    adguard = world["adguard"]
    state = {"have": adguard.have, "lists": adguard.lists, "copies": adguard.copies, "file": str(world["file"]), "groups": world["groups"].records,
             "own": world["own"].choices, "whole": sorted(world["whole"].names), "users": [{"name": u.name, "ip": u.ip} for u in world["users"]],
             "texts": {source.value: list_text(source) for source in GroupSource}}
    path = place("older-state.json")
    path.write_text(json.dumps(state))
    done = subprocess.run([sys.executable, "-c", OLDER_LISTS, str(older / "src"), str(path)], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        return {"error": done.stderr[-1500:]}, []
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    adguard.have = seen["state"]["have"]
    adguard.lists = seen["state"]["lists"]
    adguard.copies = seen["state"]["copies"]
    return seen, seen["snapshots"]


def lists_at(snapshot, addresses):
    """Each person's lists in one state of AdGuard (as StrictAdGuard reads its rules)."""
    adguard = StrictAdGuard()
    adguard.have = snapshot["have"]
    adguard.lists = snapshot["lists"]
    adguard.copies = snapshot["copies"]
    return {address: adguard.lists_of(address) for address in addresses}


def t_group_tags():
    print("\n— списки групп — метки клиентов: смена списков на месте, временный клиент, обновление со старого файла и откат к ddd7ca8")
    from awgscale.agent.adguard.group_lists import GroupSource, head_of, rule_names, sources_for
    from awgscale.agent.adguard.moves import MOVE_PROFILE
    people = [KnownUser(name=name, ip=f"10.9.7.{number}") for number, name in enumerate(["ann", "bob", "kid", "dan", "eve", "fay"], start=10)]

    def settled(world):
        broken = []
        return moves_settles(world, broken) and not broken

    # a children's group switches NSFW for social networks while social networks cannot be downloaded
    world = moves_world([{"id": "kkkkk1", "name": "K", "filter": "kids", "kids_lists": ["nsfw"], "members": ["kid", "fay"]},
                         {"id": "ppppp1", "name": "P", "filter": "pro", "members": ["ann"]}], people)
    ok(settled(world) and world["adguard"].lists_of("10.9.7.12") == {GroupSource.NSFW}, "дети с NSFW: у ребёнка NSFW", world["adguard"].have)
    broken = []
    world["groups"].records[0]["kids_lists"] = ["social"]
    moves_watch(world, broken)
    world["downloads"].hanging = {GroupSource.SOCIAL}
    said = moves_sync(world)
    moves_drain(world)
    tags = world["adguard"].have["grp-kkkkk1"]["tags"]
    ok(said is None and tags == ["device_phone", "device_tv"] and world["adguard"].lists_of("10.9.7.12") == {GroupSource.NSFW} and not broken,
       "детской группе вместо NSFW — соцсети, а их не скачать: клиент несёт обе метки, у ребёнка NSFW остаётся (соцсетей в файле нет — их метка ничего не блокирует)",
       [said, tags, broken[:1]])
    world["lists_memory"].failed_at = None
    calls = len(world["adguard"].calls)
    for _ in range(3):
        moves_sync(world)
        moves_drain(world)
        run(world["profiles"].check(world["users"]))
    ok(len(world["adguard"].calls) == calls and world["adguard"].lists_of("10.9.7.12") == {GroupSource.NSFW} and not broken,
       "пока соцсети не скачиваются, клиенты стоят: ни одного вызова к ним, NSFW у ребёнка", world["adguard"].calls[calls:])
    world["downloads"].hanging = set()
    world["lists_memory"].failed_at = None
    run(world["keeper"].keep())
    ok(world["adguard"].lists_of("10.9.7.12") == {GroupSource.NSFW, GroupSource.SOCIAL} and not broken,
       "соцсети скачались и загружены: у ребёнка на миг обе (старая метка ещё на клиенте)", world["adguard"].lists_of("10.9.7.12"))
    calls = len(world["adguard"].calls)
    moves_sync(world)
    moves_drain(world)
    ok(world["adguard"].calls[calls:] == [("update", "grp-kkkkk1", "grp-kkkkk1", ["10.9.7.12", "10.9.7.15"])] and world["adguard"].have["grp-kkkkk1"]["tags"] == ["device_phone"]
       and world["adguard"].lists_of("10.9.7.12") == {GroupSource.SOCIAL} and head_of(world["file"]).sources == {GroupSource.PRO, GroupSource.SOCIAL} and not broken,
       "и одним изменением клиента метка NSFW снята; NSFW ушёл из файла, когда его не несёт никто", [world["adguard"].calls[calls:], head_of(world["file"])])
    names = {name for source in head_of(world["file"]).sources for name in rule_names(list_text(source))}
    rules = [line for line in world["file"].read_text().splitlines() if line.startswith("||")]
    ok(len(rules) == len(names) == head_of(world["file"]).rules, "в файле правил ровно столько, сколько разных имён в его списках (память AdGuard: не по строке на список)",
       [len(rules), len(names)])

    # moves need no refresh: the calls of the block lists all fail, the clients still change
    world["adguard"].filters_failing = 1.0
    world["adguard"].rnd = __import__("random").Random(1)
    world["groups"].records[1]["members"] = ["ann", "bob"]
    world["groups"].records[0]["members"] = ["kid"]
    moves_watch(world, broken)
    said = moves_sync(world)
    moves_drain(world)
    ok(said is None and world["adguard"].serving("10.9.7.11") == "grp-pro" and world["adguard"].serving("10.9.7.15") is None and not broken,
       "блокировщик не обновляет списки (каждый вызов списков отказан или без ответа) — люди всё равно переходят, одним изменением клиентов", [said, broken[:1]])
    world["adguard"].filters_failing = 0.0

    # a stand-in carries the tags of the client its people leave
    world = moves_world([{"id": "aaaaa1", "name": "A", "filter": "kids", "kids_lists": ["nsfw"], "members": ["ann", "bob"]},
                         {"id": "bbbbb1", "name": "B", "filter": "off", "members": ["kid", "dan"]}], people)
    settled(world)
    broken = []
    world["groups"].records = [dict(world["groups"].records[0], filter="off", members=["ann", "kid"]), dict(world["groups"].records[1], filter="kids", kids_lists=["social"], members=["bob", "dan"])]
    moves_watch(world, broken)
    calls = len(world["adguard"].sized)
    moves_sync(world)
    stand_ins = [call for call in world["adguard"].sized[calls:] if call[0] == "add" and call[1].startswith(MOVE_PROFILE)]
    ok(stand_ins and all(call[2]["tags"] in (["device_tv"], []) for call in stand_ins) and not broken,
       "две группы меняются людьми и фильтрами: временный клиент — копия того, откуда уходят, с его метками (у детского — NSFW)", [stand_ins, broken[:1]])
    ok(settled(world), "затем — ровно как надо")

    # the older agent's file of names: the clients are tagged where they are first, then the file is converted
    world = moves_world([{"id": "kkkkk1", "name": "K", "filter": "kids", "kids_lists": ["nsfw", "social"], "members": ["kid"]},
                         {"id": "ppppp1", "name": "P", "filter": "pro", "members": ["ann", "bob"]}], people, own={"dan": "proplus"}, whole={"bob"})
    settled(world)
    moves_older_file(world)
    from awgscale.agent.sites.scoped import RouteDns
    world["dns"].routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8", "[/k.example/]1.1.1.1"), direct_lines=())}
    broken = []
    moves_watch(world, broken)
    calls = len(world["adguard"].calls)
    moves_sync(world)
    first = world["adguard"].calls[calls:]
    ok(first and all(call[0] == "update" and call[1] == call[2] for call in first) and all(world["adguard"].have[name]["tags"] for name in ("grp-kkkkk1", "grp-pro", "grp-pro-all", "grp-proplus"))
       and not broken, "файл прежнего агента в работе: сначала клиенты только получают метки на месте (никто никуда не переходит, вариант детей ещё не заведён)", [first, broken[:1]])
    moves_drain(world)
    head = head_of(world["file"])
    ok(head.kind.value == "tags" and not broken and world["adguard"].lists_of("10.9.7.12") == {GroupSource.NSFW, GroupSource.SOCIAL},
       "затем файл переведён в метки на месте и скачан заново — у ребёнка оба его списка всё время", [head, broken[:1]])
    ok(settled(world) and world["adguard"].serving("10.9.7.12") == "grp-kkkkk1-rkkkkk1" and not broken, "и только потом ребёнок — в варианте своей группы, со списками", world["adguard"].have)

    # the older file, and AdGuard never answers a refresh in time (one slow list of its own), though it does each:
    # the conversion is known to be read from AdGuard's list itself, and people move all the same
    world = moves_world([{"id": "kkkkk1", "name": "K", "filter": "kids", "kids_lists": ["nsfw"], "members": ["kid"]},
                         {"id": "ppppp1", "name": "P", "filter": "pro", "members": ["ann"]}], people)
    settled(world)
    moves_older_file(world)
    lost = StrictAdGuard.refresh

    async def late(adguard):
        adguard.list_calls.append(("refresh",))
        for f in adguard.lists:
            if f["enabled"]:
                adguard.read_file(f["url"])
        from awgscale.agent.adguard.client import AdGuardDown
        raise AdGuardDown("timed out", "ReadTimeout")
    StrictAdGuard.refresh = late
    try:
        world["groups"].records[1]["members"] = ["ann", "bob"]
        world["groups"].records[0]["members"] = ["kid", "dan"]
        broken = []
        moves_watch(world, broken)
        for _ in range(4):
            moves_sync(world)
            moves_drain(world)
            world["lists_memory"].failed_at = None
        ok(world["adguard"].serving("10.9.7.11") == "grp-pro" and world["adguard"].serving("10.9.7.13") == "grp-kkkkk1" and not broken
           and head_of(world["file"]).kind.value == "tags" and not world["file"].with_name("group-lists.txt.unread").exists(),
           "файл прежнего агента, а AdGuard ни разу не отвечает на обновление вовремя (но делает его): что файл прочитан, видно по самому списку в AdGuard — "
           "люди переходят, никто не теряет списков", [broken[:1], world["adguard"].have])
    finally:
        StrictAdGuard.refresh = lost

    # a refresh, or a download, that waits a long time holds no change of the clients
    world = moves_world([{"id": "kkkkk1", "name": "K", "filter": "kids", "kids_lists": ["nsfw"], "members": ["kid"]},
                         {"id": "ppppp1", "name": "P", "filter": "pro", "members": ["ann"]}], people)
    settled(world)
    for what in ("refresh", "download"):
        parked = asyncio.Event()
        held_up = []
        if what == "refresh":
            async def slow(adguard=world["adguard"]):
                held_up.append("refresh")
                await parked.wait()
            world["adguard"].refresh = slow
        else:
            async def slow_text(source):
                held_up.append(source.value)
                await parked.wait()
                return list_text(source)
            world["downloads"].text = slow_text
        world["groups"].records[0]["kids_lists"] = ["nsfw", "social"] if what == "refresh" else ["nsfw", "gambling"]
        world["lists_memory"].failed_at = None
        moves_sync(world)
        for _ in range(20):
            run(asyncio.sleep(0))
        world["groups"].records[1]["members"] = ["ann", "eve"] if what == "refresh" else ["ann", "eve", "fay"]
        said = moves_sync(world)
        ok(held_up and said is None and world["adguard"].serving("10.9.7.14") == "grp-pro" and not world["keeper"].task.done(),
           f"{'обновление списков в AdGuard' if what == 'refresh' else 'скачивание списка'} висит — изменение клиентов всё равно проходит (Ева в Pro)", [held_up, said])
        parked.set()
        moves_drain(world)
        vars(world["adguard"]).pop("refresh", None)
        vars(world["downloads"]).pop("text", None)
    ok(settled(world), "отпустили — всё как надо")

    # rollback to ddd7ca8 and back
    world = moves_world([{"id": "kkkkk1", "name": "K", "filter": "kids", "kids_lists": ["nsfw", "social"], "members": ["kid", "fay"]},
                         {"id": "ppppp1", "name": "P", "filter": "pro", "members": ["ann", "bob"]}], people, own={"dan": "proplus"}, whole={"bob"})
    settled(world)
    addresses = [person.ip for person in people]
    before = {address: world["adguard"].lists_of(address) for address in addresses}
    seen, snapshots = older_agent(world)
    lacking = [(index, address, before[address] - lists[address]) for index, snapshot in enumerate(snapshots) for address, lists in [(None, None)] if False]
    for index, snapshot in enumerate(snapshots):
        now = lists_at(snapshot, addresses)
        lacking += [(index, snapshot["call"], address, before[address] - now[address]) for address in addresses if before[address] - now[address]]
    ok("error" not in seen and not lacking and not [call for call in seen.get("calls", []) if call[0] in ("add", "delete")]
       and all(client["tags"] for name, client in world["adguard"].have.items() if name != "grp-own-off") and "$client=" in world["adguard"].copies.get(str(world["file"]), ""),
       "откат к ddd7ca8: его синхронизация меток не видит и клиентов не трогает (метки остаются), его файл имён записан и загружен; ни после одного его вызова "
       "никто не потерял списка", [seen.get("error"), seen.get("calls"), lacking[:3]])
    broken = []
    ok(settled(world) and head_of(world["file"]).kind.value == "tags", "и снова эта версия: клиенты помечены на месте, файл переведён в метки, всё как надо", broken[:1])
    world["dns"].routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8", "[/k.example/]1.1.1.1"), direct_lines=())}
    settled(world)
    before = {address: world["adguard"].lists_of(address) for address in addresses}
    held_before = {address: world["adguard"].serving(address) for address in addresses}
    seen, snapshots = older_agent(world)
    out = sorted({(snapshot["call"][1], address) for snapshot in snapshots for address in addresses
                  if held_before[address] and "-r" in held_before[address] and StrictAdGuard.serving(type("A", (), {"have": snapshot["have"]})(), address) is None})
    ok("error" not in seen and out and settled(world),
       "откат к ddd7ca8 с вариантами клиентов (правило для группы): он их не знает и удаляет, а клиента группы заводит заново — между этими вызовами её люди ни в "
       "каком клиенте (так он делает всегда: docs/rollback.md); эта версия потом всё ставит как надо", [seen.get("calls"), out[:3]])


def moves_converge(adguard, want, runs=3):
    """ClientMoves straight on this blocker, `runs` times undisturbed: → (the first ended with nothing refused and the
    blocker exactly as wanted, the later ones made no call, no call AdGuard would refuse, what each said)."""
    from awgscale.agent.adguard.moves import ClientMoves
    outcomes = []
    for _ in range(runs):
        calls = len(adguard.calls)
        said = run(ClientMoves(adguard, want, json.loads(json.dumps(adguard.have))).run())
        outcomes.append((said, len(adguard.calls) - calls))
    exact = set(adguard.have) == {profile.name for profile in want} and all(sorted(adguard.have[profile.name]["ids"]) == sorted(profile.ids) for profile in want)
    return outcomes[0][0] == [] and exact, all(calls == 0 for _, calls in outcomes[1:]), not adguard.invalid, outcomes


def t_client_room():
    print("\n— клиенты на пределе размера запроса: переезд всё равно доходит до конца, и ни одного вызова, который блокировщик отверг бы сам")
    from awgscale.agent.adguard.moves import MOVE_REQUEST_BYTES, ClientMoves, request_bytes
    from awgscale.agent.adguard.profiles import asks_own, fitting, special_profile
    from awgscale.agent.adguard.models import Profile

    tunnel = [f"192.168.100.{number}" for number in range(2, 255)]

    def routed(ids, lines):
        return asks_own(special_profile("grp-raaaaa1", sorted(ids), "kids"), [f"[/s{number}.example/]10.200.{number % 250}.1" for number in range(lines)])

    # r5-0 full_leavers_192.py: a wanted client left full (a stopped change) whose people all leave to ready clients
    lines = 1500
    while request_bytes(routed([tunnel[248]], lines + 1)) <= 60000:
        lines += 1
    people = tunnel[:248]
    count = len(people)
    while request_bytes(routed(people[:count], lines)) > MOVE_REQUEST_BYTES:
        count -= 1
    held = routed(people[:count], lines)
    adguard = StrictAdGuard()
    for profile in (held, special_profile("grp-bbbbb1", [tunnel[248]], "kids"), special_profile("grp-ccccc1", [tunnel[249]], "kids")):
        adguard.have[profile.name] = profile.wire()
    half = len(held.ids) // 2
    want = [routed([tunnel[248]], lines), special_profile("grp-bbbbb1", sorted(held.ids[:half]), "kids"),
            special_profile("grp-ccccc1", sorted([tunnel[249], *held.ids[half:]]), "kids")]
    exact, quiet, valid, outcomes = moves_converge(adguard, want)
    ok(exact and quiet and valid,
       f"r5-0 full_leavers_192: нужный клиент держит {count} человек ({request_bytes(held)} байт), все уходят в готовые клиенты, а его новичок — у другого: "
       "отпускает их тем же вызовом, что берёт новичка; временного клиента для уже принятых нет, отказов нет, потом ни одного вызова", [outcomes, adguard.invalid[:1]])

    # r5-0 ratchet.py 1919: only legal wanted states, each change stopped at the variant's release, until it is full
    class Refuser(StrictAdGuard):
        armed = False

        async def update(self, name, profile):
            if self.armed and name == "grp-raaaaa1" and set(self.have[name]["ids"]) - set(profile.ids) - {i for i in self.have[name]["ids"] if "/" in i}:
                self.refuse_at = len(self.calls)
                self.armed = False
            return await super().update(name, profile)

    lines = 1919
    adguard = Refuser()
    step = 1
    while request_bytes(routed(tunnel[-(step + 1):], lines)) <= 60000:
        step += 1
    adguard.have = {"grp-raaaaa1": routed(tunnel[:step], lines).wire(), "grp-h": special_profile("grp-h", sorted(tunnel[step:250]), "kids").wire()}
    free = tunnel[step:250]
    stops = 0
    last = -1
    while len(free) >= step + 2 and not (stops and len(adguard.have["grp-raaaaa1"]["ids"]) == last):
        last = len(adguard.have["grp-raaaaa1"]["ids"])
        new = free[:step]
        free = free[step:]
        adguard.armed = True
        said = run(ClientMoves(adguard, [routed(new, lines), special_profile("grp-h", sorted(free), "kids")], json.loads(json.dumps(adguard.have))).run())
        adguard.refuse_at = None
        stops += bool(said)
    held = sorted({identifier.split("/")[0] for identifier in adguard.have["grp-raaaaa1"]["ids"]})
    newcomer = next((address for address in free if len(address) == 15), free[0])
    adguard.have["grp-g"] = special_profile("grp-g", [tunnel[-1]], "kids").wire()
    want = [routed([newcomer], lines), special_profile("grp-h", sorted(set(free) - {newcomer} | set(held[: len(held) // 2])), "kids"),
            special_profile("grp-g", sorted([tunnel[-1], *held[len(held) // 2:]]), "kids")]
    invalid = len(adguard.invalid)
    exact, quiet, valid, outcomes = moves_converge(adguard, want)
    ok(stops >= 40 and exact and quiet and len(adguard.invalid) == invalid,
       f"r5-0 ratchet 1919: {stops} изменений остановлены на отпускании вариантом людей — он набит до {request_bytes(routed(held, lines))} байт; затем его люди уходят, "
       "а нужен ему один новичок: следующий раз ровно как надо, без вызова, который блокировщик отверг бы, потом ни одного вызова", [outcomes, adguard.invalid[invalid:invalid + 1]])

    # r5-0 over_move_limit.py and r5-2 band.py: a client as wanted between 64 000 and 65 536 bytes
    def addresses(count):
        return [f"10.{10 + number // 250}.{number % 250}.2" for number in range(count)]
    users = [KnownUser(name=f"u{number}", ip=address) for number, address in enumerate(addresses(4750))]
    want = fitting([UserGroup(id="kkkkk1", name="K", filter="kids", members=[user.name for user in users])], users).profiles
    for start, have in (("с пустого", {}), ("из grp-pro", {"grp-pro": want[0].model_copy(update={"name": "grp-pro", "parental_enabled": False, "safe_search": {"enabled": False}}).wire()})):
        adguard = StrictAdGuard()
        adguard.have = json.loads(json.dumps(have))
        exact, quiet, valid, outcomes = moves_converge(adguard, want)
        ok(MOVE_REQUEST_BYTES < request_bytes(want[0]) <= 65536 and exact and quiet and valid,
           f"r5-0 over_move_limit: детский клиент {len(want[0].ids)} человек, {request_bytes(want[0])} байт (больше {MOVE_REQUEST_BYTES}, но AdGuard его берёт), {start}: "
           "ровно как надо, потом ни одного вызова", [[(said[:1], calls) for said, calls in outcomes], {name: len(client["ids"]) for name, client in adguard.have.items()}])

    def level(name, ids):
        return Profile(name=name, ids=sorted(ids), filtering_enabled=True, parental_enabled=False, safebrowsing_enabled=True,
                       safe_search={"enabled": False, **{engine: True for engine in ("bing", "duckduckgo", "ecosia", "google", "pixabay", "yandex", "youtube")}})
    pool = [f"10.{64 + number // 60000}.{(number // 250) % 256}.{number % 250 + 2}" for number in range(6000)]
    count = 4000
    while request_bytes(level("grp-pro", pool[:count])) <= 64600:
        count += 10
    want = [level("grp-pro", pool[:count])]
    for start, have in (("с пустого", {}), ("из grp-std", {"grp-std": level("grp-std", pool[:count]).model_copy(update={"safebrowsing_enabled": False}).wire()})):
        adguard = StrictAdGuard()
        adguard.have = json.loads(json.dumps(have))
        exact, quiet, valid, outcomes = moves_converge(adguard, want)
        ok(exact and quiet and valid, f"r5-2 band: grp-pro {count} человек, {request_bytes(want[0])} байт, {start}: ровно как надо, потом ни одного вызова",
           [[(said[:1], calls) for said, calls in outcomes], {name: len(client["ids"]) for name, client in adguard.have.items()}])


# ── the sites ───────────────────────────────────────────────────────────────
def t_sites():
    print("\n— сайты: режим, который не помещается в блокировщик; probe с плохим именем")
    switched = []

    class Store:
        async def read(self):
            return [SiteGroup(id="aaaaaa", name="Много", on=True, items=tuple(f"site{number}.example.net" for number in range(50)))]

    class Modes:
        async def read(self):
            return RouteMode.FOREIGN

        async def save(self, mode):
            switched.append(("saved", mode))

    class Routing:
        def mode(self):
            return RouteMode.FOREIGN

        async def switch(self, mode, entries):
            switched.append(("switch", mode))

    service = SitesService(Store(), Modes(), Routing(), None, None, None, None, place("cascade.conf"), place("sites.lock"), upstream_bytes=200)
    error = refusal(service.set_mode(ModeSet(mode=RouteMode.LIST)))
    ok(error is not None and error.startswith("слишком много адресов через заграницу (") and error.endswith("блокировщик столько не принимает") and switched == [],
       "список не помещается в блокировщик: отказ до любых изменений", [error, switched])

    for host, why in (("bad host.com", "пробел в имени"), ("localhost", "имя без точки"), ("-x.com", "метка с дефиса"), ("exa_mple.com", "подчёркивание"),
                      ("example.com/..", "путь в имени"), ("example.com:22", "порт в имени"), ("http://example.com", "ссылка вместо имени"),
                      ("", "пусто"), ("a." * 130 + "com", "длиннее 253 знаков"), ("пример.рф", "не-ASCII имя"), ("example.com;id", "знак ;"),
                      ("$(id).example.com", "подстановка оболочки")):
        error = refusal(service.probe(Probe.model_construct(host=host, path=None)))
        ok(error == "некорректное имя", f"probe: {why} — «некорректное имя»", error)
    for path, why in (("status", "путь не с косой черты"), ("/a b", "пробел в пути"), ("/a\"b", "кавычка в пути"), ("/a\nb", "перевод строки внутри пути"),
                      ("/a#b", "решётка в пути"), ("/a\\b", "обратная черта"), ("/" + "a" * 601, "путь длиннее 601 знака"), ("", "пустой путь"), ("/ok\n", "перевод строки в конце пути")):
        error = refusal(service.probe(Probe.model_construct(host="example.com", path=path)))
        ok(error == "некорректное имя", f"probe: {why} — «некорректное имя»", error)

    print("\n— probe: что видит посетитель по ответу curl")
    for stdout, code, state, text in (("200 0.25", 0, "ok", "открывается"), ("403 0.1", 0, "refused", "сайт отказывает (403)"),
                                      ("", TIMED_OUT, "dead", "не отвечает"), ("мусор", 0, "dead", "не отвечает"), ("200 x", 0, "dead", "не отвечает")):
        async def curl(command, given, timeout, out=stdout, rc=code):
            return Result(rc, out, "")
        seen = run(CurlProber(curl).probe("example.com", "8.8.8.8", None))
        ok(seen.state.value == state and seen.text == text and (state != "dead" or seen.code == 0), f"probe_one: curl ответил «{stdout}» (код выхода {code}) → {state}, «{text}»", seen)
    ok(CURL_TIMED_OUT == 28, "непонятный ответ curl читается как «не отвечает» (код 28)")


# ── files that are not there ────────────────────────────────────────────────
def t_missing_files():
    print("\n— файлов нет")
    ok(listed_lines(place("no-such-list.txt")) == [], "скомпилированного списка нет — пустой список, а не ошибка")
    ok(CompiledExits(place("no-cascade.conf")).weights() == {}, "cascade.conf нет — долей выходов нет")
    ok(unexpired({"not": "a list"}, datetime.now(UTC)) == [] and unexpired(None, datetime.now(UTC)) == [], "приглашения в хранилище не списком — их нет")


# ── the users' groups ───────────────────────────────────────────────────────
def t_regrouping():
    print("\n— группы пользователей: приглашения и списки детского режима")
    ok(invitations_left("not a list", {"aaaaaa"}) is None, "приглашения не списком — ничего не убирается")
    ok(invitations_left([{"group": "aaaaaa"}, {"group": None}], {"aaaaaa"}) is None, "все группы приглашений есть — ничего не убирается")
    for junk in ("kids", ["no-such-list"], [5], {"a": 1}, None):
        try:
            chosen_kids_lists({"kids_lists": junk}, "Дети", "kkkkk1", {}, {})
            error = None
        except Refused as e:
            error = e.error
        ok(error == "Дети: таких списков детского режима нет", f"kids_lists = {junk!r} — отказ", error)
    ok(chosen_kids_lists({}, "Дети", "kkkkk1", {"kkkkk1": "kids"}, {"kkkkk1": ("x",)}) == ("x",),
       "страница списков не прислала, группа и была детской — списки остаются сохранёнными")
    ok(chosen_kids_lists({}, "Дети", "kkkkk1", {"kkkkk1": "kids"}, {"kkkkk1": None}) is None, "…и у записи из времени до списков их так и нет")


# ── pauses and pins ─────────────────────────────────────────────────────────
def t_pauses_and_pins():
    print("\n— приостановки и закрепления, когда список пользователей не прочитан")
    since = datetime(2026, 11, 1, tzinfo=UTC)
    commands = []

    class PauseStore:
        def __init__(self, pauses):
            self.pauses = dict(pauses)
            self.saved = 0

        async def read(self):
            return dict(self.pauses)

        async def save(self, pauses):
            self.saved += 1
            self.pauses = dict(pauses)

    async def kernel(command, given=None, timeout=None):
        commands.append(command)
        return Result(1, "", "The set with the given name does not exist")

    store = PauseStore({"bob": since, "eve": since})
    pauses = Pauses(store, kernel, "awg0", place("paused.txt"))
    ok(run(pauses.sync([])) == ["bob", "eve"] and commands == [] and store.saved == 0 and not place("paused.txt").exists(),
       "список пользователей пуст (не прочитан) — ядро и хранилище оставлены как есть", [commands, store.saved])
    store = PauseStore({"bob": since})
    pauses = Pauses(store, kernel, "awg0", place("paused.txt"))
    got = run(pauses.sync([KnownUser(name="bob", ip=""), KnownUser(name="alice", ip="10.9.9.4")]))
    ok(got == ["bob"] and [command[:2] for command in commands] == [["ipset", "list"]], "набора приостановок в ядре ещё нет и держать нечего — он и не создаётся", commands)

    pins_file = place("pins.map")
    pins_file.write_text("10.9.9.4 1\n")
    asked = []

    class HandPins:
        async def read(self):
            return {"bob": 3}

        async def whole(self):
            return set()

    class Watch:
        async def pin(self, timeout):
            asked.append(timeout)

    group = StoredGroups.of([{"id": "aaaaaa", "name": "Семья", "exit": 1, "members": ["alice"]}]).groups[0]
    done = run(Pinning(HandPins(), Watch(), pins_file).compile([group], []))
    ok(done is False and pins_file.read_text() == "10.9.9.4 1\n" and asked == [], "список пользователей не прочитан — файл закреплений не опустошается, сторож не дёргается", [done, asked])


# ── the notes of the last look ──────────────────────────────────────────────
def t_seen():
    print("\n— заметки прошлого взгляда: взгляд, начатый раньше, приходит позже")
    now = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    later = int((now + timedelta(seconds=30)).timestamp())
    saved = []

    class Store:
        async def load(self):
            return {"at": later}

        async def save(self, record):
            saved.append(record)

    class Together:
        @asynccontextmanager
        async def whole(self):
            yield

    journal = Journal()
    run(SeenNotes(Store(), Together(), journal, place("seen.lock")).check([KnownUser(name="bob", ip="10.9.9.3")], [], now))
    ok(saved == [] and journal.lines == [], "заметки уже записал взгляд, начатый позже, — этот ничего не пишет", [saved, journal.lines])


# ── a new exit ──────────────────────────────────────────────────────────────
def t_exit_add():
    print("\n— новый выход: сервис выходов не начал работу")
    settings = conf.parse(open(os.path.join(ROOT, "tests", "cascade.conf")).read())

    class Settings:
        async def load(self):
            return settings

    class Exits:
        def __init__(self, said):
            self.said = said
            self.asked = []

        async def registry(self):
            return Registry()

        async def add(self, index, name, share, address, ssh_port, password):
            self.asked.append((index, name, address))
            return Outcome(ok=False, said=self.said)

    request = ExitAdd(payload=b64({"address": "203.0.113.50", "name": "exit-de"}))
    for said, expected, why in (("", "сервис выходов не отвечает", "промолчал"), ("уже идёт другая работа", "уже идёт другая работа", "сказал почему")):
        exits = Exits(said)
        error = refusal(ExitServers(None, Settings(), exits, None, None, None).add(request, None))
        ok(error == expected and len(exits.asked) == 1, f"сервис выходов {why} — отказ «{expected}»", [error, exits.asked])


def t_cascade_document():
    print("\n— настройки каскада не того вида")
    for document in ([], "text", None, 5):
        try:
            checked(document)
            error = None
        except ValueError as e:
            error = str(e)
        ok(error == "настройки каскада: не тот вид", f"документ {document!r} — «настройки каскада: не тот вид»", error)


# ── the invitations ─────────────────────────────────────────────────────────
class Invitations:
    def __init__(self):
        self.saved = []
        self.reads = 0
        self.vanish_after = None

    async def read(self):
        self.reads += 1
        if self.vanish_after is not None and self.reads > self.vanish_after:
            return []
        return list(self.saved)

    async def save(self, invitations):
        self.saved = list(invitations)


class Notes:
    def __init__(self, broken):
        self.broken = broken
        self.lines = []

    async def made(self, text, caller):
        if self.broken:
            raise OSError("journald away")
        self.lines.append(text)

    async def used(self, text):
        if self.broken:
            raise OSError("journald away")
        self.lines.append(text)


class Directory:
    def __init__(self, before, after):
        self.answers = [before, after]
        self.broken = False

    async def list(self, force=False):
        if self.broken:
            raise OSError("users service away")
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class Newcomers:
    def __init__(self):
        self.done = []

    async def ready(self):
        self.done.append("ready")

    async def forget(self, name):
        self.done.append("forget")

    async def add(self, name):
        self.done.append("add")
        return True

    async def pin(self, groups, users):
        self.done.append("pin")

    async def filter(self, groups, users):
        self.done.append("filter")


class Watch:
    def __init__(self, broken):
        self.broken = broken

    async def pin(self, timeout):
        if self.broken:
            raise OSError("watch away")


class UsersGate:
    async def config(self, name):
        return {"name": name, "conf": "[Interface]"}


def invitations_world(group=None, broken=False, members=()):
    records = [{"id": "aaaaaa", "name": "Семья", "exit": 1, "filter": "kids", "members": list(members)}]
    world = {
        "invites": Invitations(),
        "groups": Groups(records),
        "directory": Directory([KnownUser(name="alice", ip="10.9.9.4")], [KnownUser(name="alice", ip="10.9.9.4"), KnownUser(name="eve", ip="10.9.9.5")]),
        "newcomers": Newcomers(),
        "notes": Notes(broken),
    }
    service = InvitesService(world["invites"], world["groups"], world["directory"], UsersGate(), world["newcomers"], Watch(broken), None, None, world["notes"],
                             place("users.lock"), place("invite.lock"))
    made = run(service.add(InviteAdd(payload=b64({"group": group, "uses": 2, "days": 7})), Caller.PANEL))
    return service, world, made


def t_invitations():
    print("\n— приглашения: журнал и сторож недоступны, ссылка пропала по дороге, группа")
    service, world, made = invitations_world(broken=True)
    ok(len(world["invites"].saved) == 1 and world["notes"].lines == [], "журнал не принял строку о создании — приглашение всё равно создано")
    redeemed = run(service.redeem(InviteRedeem(token=made.token, name="eve")))
    ok(redeemed.left == 1 and redeemed.payload["name"] == "eve", "журнал и сторож маршрутов недоступны — пользователь всё равно создан и получает свой код", redeemed)
    revoked = run(service.remove(InviteRemove(invite_id=made.id), Caller.PANEL))
    ok(revoked.invites == [] and world["invites"].saved == [], "журнал не принял строку об отзыве — приглашение всё равно отозвано", revoked)

    service, world, made = invitations_world()
    world["directory"].broken = True
    error = refusal(service.redeem(InviteRedeem(token=made.token, name="eve")))
    ok(error == "Сейчас подключить нельзя — попробуйте ещё раз чуть позже" and "add" not in world["newcomers"].done,
       "список пользователей не читается — подключить нельзя, никого не создаём", [error, world["newcomers"].done])

    service, world, made = invitations_world()
    world["invites"].vanish_after = world["invites"].reads + 1
    error = refusal(service.redeem(InviteRedeem(token=made.token, name="eve")))
    ok(error == GONE and "add" not in world["newcomers"].done, "ссылку отозвали, пока гость ждал очереди, — «ссылка недействительна», никто не создан", [error, world["newcomers"].done])

    service, world, made = invitations_world(group="aaaaaa", members=("eve",))
    redeemed = run(service.redeem(InviteRedeem(token=made.token, name="eve")))
    ok(redeemed.group is None and world["groups"].records[0]["members"] == ["eve"] and "pin" not in world["newcomers"].done,
       "имя уже числится в группе — второй раз не добавляется, группа в ответе не названа", [redeemed, world["groups"].records])

    service, world, made = invitations_world(group="aaaaaa")
    world["groups"].broken = True
    redeemed = run(service.redeem(InviteRedeem(token=made.token, name="eve")))
    ok(redeemed.group is None and redeemed.payload["name"] == "eve" and world["notes"].lines[-1] == "По приглашению создан пользователь eve",
       "группа не сохранилась — пользователь всё равно создан; закрепления и фильтр поправит следующий опрос", [redeemed, world["notes"].lines])

    service, world, made = invitations_world(group="aaaaaa")
    redeemed = run(service.redeem(InviteRedeem(token=made.token, name="eve")))
    ok(redeemed.group == "Семья" and world["groups"].records[0]["members"] == ["eve"] and world["newcomers"].done[-2:] == ["pin", "filter"]
       and world["notes"].lines[-1] == "По приглашению создан пользователь eve в группе «Семья»",
       "обычный случай с группой: гость в группе, закреплён за её выходом, под её фильтром, в журнале группа названа", [redeemed, world["newcomers"].done, world["notes"].lines])


def main():
    asyncio.to_thread = inline_thread
    logging.getLogger("awgscale.agent").setLevel(logging.ERROR)       # the keeper of the lists says each failure the model makes
    t_profiles()
    t_profiles_routes()
    t_client_moves()
    t_client_room()
    t_group_tags()
    t_sites()
    t_missing_files()
    t_regrouping()
    t_pauses_and_pins()
    t_seen()
    t_exit_add()
    t_cascade_document()
    t_invitations()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
