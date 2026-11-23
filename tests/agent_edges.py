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
import os
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
        routed = None

        def nudge(self, groups, own=None, routed=()):
            Lists.routed = sorted(routed)

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
    ok(Lists.routed == ["hhhhh1", "kkkkk1"], "списки групп по-прежнему называют её варианты: вернётся — они уже на месте", Lists.routed)
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


class StrictAdGuard:
    """AdGuard's clients as v0.107.80 keeps them (tests/stack_adguard.py checks each of these on the real binary): an
    address and a network ("10.0.0.2/32") are held apart, a person is filtered by the client of their address first and
    by the one of their network after it (also when the client of the address is deleted); an address or a network
    another client holds, a client with nothing in it, a name taken or missing, a request over `limit` bytes — refused. With `failing`, a call fails at random: refused, or done with its answer lost
    (a timeout); `held` too. `seen` is called after every call, failed or not."""

    def __init__(self, rnd=None, failing=0.0, limit=65536):
        self.have = {}
        self.calls = []
        self.sized = []                 # every add and update as sent: (call, …, the client)
        self.limit = limit              # AdGuard refuses a request over 65 536 bytes
        self.rnd = rnd
        self.failing = failing
        self.seen = None
        self.refuse_at = None           # the number of the call (from 0) that is refused, or answered and lost
        self.lose_at = None

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
        fate = self.fate()
        if valid and fate != "refused":
            change()
        if self.seen:
            self.seen(self.calls[-1])
        return Outcome(ok=valid and fate == "done", said="" if valid and fate == "done" else f"{self.calls[-1][0]}: 400")

    async def held(self):
        if self.failing and self.rnd.random() < self.failing / 3:
            from awgscale.agent.adguard.client import AdGuardDown
            raise AdGuardDown("timed out", "timeout")
        return json.loads(json.dumps(self.have))

    def fits(self, profile):
        from awgscale.agent.adguard.moves import request_bytes
        return request_bytes(profile) <= self.limit

    async def add(self, profile):
        self.calls.append(("add", profile.name, list(profile.ids)))
        self.sized.append(("add", profile.name, profile.wire()))
        valid = profile.name not in self.have and bool(profile.ids) and not self.clash(profile.ids, None) and self.fits(profile)
        return self.answer(valid, lambda: self.have.__setitem__(profile.name, profile.wire()))

    async def update(self, name, profile):
        self.calls.append(("update", name, profile.name, list(profile.ids)))
        self.sized.append(("update", name, profile.name, profile.wire()))
        valid = (name in self.have and (profile.name == name or profile.name not in self.have) and bool(profile.ids) and not self.clash(profile.ids, name)
                 and self.fits(profile))

        def change():
            del self.have[name]
            self.have[profile.name] = profile.wire()
        return self.answer(valid, change)

    async def delete(self, name):
        self.calls.append(("delete", name))
        return self.answer(name in self.have, lambda: self.have.pop(name))


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


class MovesDns:
    def __init__(self, routes):
        self.routes = dict(routes or {})
        self.broken = False

    async def of(self):
        if self.broken:
            raise OSError("rules store away")
        return dict(self.routes)


def moves_world(seed_groups, users, own=None, whole=(), routes=None):
    """AdGuardProfiles over a StrictAdGuard, with what the groups, own choices, whole-traffic people and rules are."""
    adguard = StrictAdGuard()
    groups = Groups(seed_groups)
    world = {"adguard": adguard, "groups": groups, "own": MovesOwn(own), "whole": MovesWhole(whole), "dns": MovesDns(routes), "users": users}
    world["journal"] = Journal()
    world["profiles"] = AdGuardProfiles(adguard, groups, world["journal"], place("moves.lock"), ProfileMemory(), own=world["own"], routes=world["whole"], dns=world["dns"])
    return world


def moves_wanted(world):
    from awgscale.agent.adguard.profiles import fitting
    return fitting([UserGroupOf(record) for record in world["groups"].records], world["users"], world["own"].choices, world["whole"].names, world["dns"].routes).profiles or []


def moves_lists(world):
    """{client: the block lists that name it}, as adguard.group_lists names them for these groups."""
    from awgscale.agent.adguard.group_lists import needs_of
    return needs_of([UserGroupOf(record) for record in world["groups"].records], world["own"].choices, set(world["dns"].routes))


def moves_key(name, client):
    """What a client gives its people: its name (the lists that name it) and everything else the agent sends."""
    from awgscale.agent.adguard.moves import as_profile, settings_of
    return None if name is None else (name, settings_of(as_profile(name, client)))


def moves_watch(world, broken, parked=None):
    """From now until the next call of this: after every call to the blocker, each person is filtered exactly as before
    the change or exactly as wanted — the same settings, and the same lists by the client's name (as the lists named
    it before the change or name it now); someone who was in no client and is wanted in none stays out. A whole-traffic
    person who was and stays one never asks other than the exit's resolvers. Breaches go to `broken`; a temporary
    client that holds someone with exactly their settings but without the lists of their client (a cycle of clients
    every one of which some list names: adguard.moves) goes to `parked`, when given, rather than to `broken`."""
    from awgscale.agent.adguard.moves import MOVE_PROFILE
    from awgscale.agent.adguard.profiles import WHOLE_UPSTREAMS
    adguard = world["adguard"]
    want = {profile.name: profile for profile in moves_wanted(world)}
    final = {address: profile.name for profile in want.values() for address in profile.ids}
    lists_now = moves_lists(world)
    lists_before = world.get("lists", lists_now)
    addresses = {address.split("/")[0] for client in adguard.have.values() for address in client["ids"]} | set(final)
    before = {address: moves_key(adguard.serving(address), adguard.have.get(adguard.serving(address))) for address in addresses}
    after = {address: (final[address], moves_key(final[address], want[final[address]].wire())[1]) if address in final else None for address in addresses}
    exit_dns = list(WHOLE_UPSTREAMS)

    def lists(name):
        return {lists_before.get(name, ()), lists_now.get(name, ())}

    def matches(now, reference):
        if now is None or reference is None:
            return now is reference
        return now[1] == reference[1] and bool(lists(now[0]) & lists(reference[0]))

    def asks_exit(key):
        return key is not None and json.loads(key[1]).get("upstreams", [])[:len(exit_dns)] == exit_dns

    def seen(call):
        for address in addresses:
            name = adguard.serving(address)
            now = moves_key(name, adguard.have.get(name))
            if asks_exit(before[address]) and asks_exit(after[address]) and not asks_exit(now):
                broken.append({"whole-traffic person off the exit's resolvers": address, "in": name, "after": call, "calls": list(adguard.calls)})
            if matches(now, before[address]) or matches(now, after[address]):
                continue
            if parked is not None and name.startswith(MOVE_PROFILE) and before[address] is not None and now[1] == before[address][1] and lists_now.get(before[address][0]):
                parked.append({"address": address, "was": before[address][0], "in": name, "after": call})
                continue
            broken.append({"address": address, "was": before[address], "now": now, "wanted": after[address], "after": call, "calls": list(adguard.calls)})
    adguard.seen = seen


def moves_exact(world):
    """Does the blocker hold exactly the clients wanted, every one as wanted, every person as their address."""
    from awgscale.agent.adguard.moves import same
    want = {profile.name: profile for profile in moves_wanted(world)}
    have = world["adguard"].have
    return set(have) == set(want) and all(same(have[name], want[name].wire()) for name in want)


def moves_sync(world):
    said = run(world["profiles"].sync([UserGroupOf(record) for record in world["groups"].records], world["users"]))
    world["lists"] = moves_lists(world)
    return said


def moves_settles(world, broken, parked=None):
    """Three undisturbed changes: the first leaves the blocker exactly as wanted, the next two make no call at all."""
    adguard = world["adguard"]
    adguard.failing = 0.0
    adguard.refuse_at = None
    adguard.lose_at = None
    outcomes = []
    for number in range(3):
        moves_watch(world, broken, parked)
        calls = len(adguard.calls)
        said = moves_sync(world)
        outcomes.append(said is None and moves_exact(world) and (number == 0 or len(adguard.calls) == calls))
    return all(outcomes)


def moves_copy(world):
    """The same groups, choices, rules and blocker, apart."""
    import copy
    twin = moves_world(copy.deepcopy(world["groups"].records), world["users"], dict(world["own"].choices), set(world["whole"].names), dict(world["dns"].routes))
    twin["adguard"].have = json.loads(json.dumps(world["adguard"].have))
    twin["adguard"].rnd = world["adguard"].rnd
    if "lists" in world:
        twin["lists"] = dict(world["lists"])
    return twin


def moves_every_stop(name, before, after, results, start=None):
    """A change from `before` to `after` made again and again, every one of its calls refused once, then answered and
    lost once (`start`, when given, first leaves the blocker as some earlier stopped change did): after every call
    everybody is filtered as before or as wanted, and the undisturbed changes that follow end exactly as wanted, then
    make no calls."""
    clean = before()
    moves_sync(clean)
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
            if start:
                start(world)
            world["adguard"].calls.clear()
            after(world)
            broken = []
            parked = []
            moves_watch(world, broken, parked)
            setattr(world["adguard"], how, number)
            moves_sync(world)
            setattr(world["adguard"], how, None)
            settled = moves_settles(world, broken, parked)
            results.append((name, number, how, broken, settled, parked))
    return total


MOVES_FILTERS = ["std", "off", "kids", "pro", "proplus", "ultimate"]
MOVES_GROUPS = ["aaaaa1", "bbbbb1", "ccccc1", "ddddd1", "eeeee1"]
MOVES_KIDS_LISTS = ["nsfw", "gambling", "bypass", "social"]


def moves_route(rnd, group):
    from awgscale.agent.sites.scoped import RouteDns
    return RouteDns(upstreams=("8.8.8.8", f"[/{group}.example/]1.1.1.{rnd.randint(1, 2)}"), direct_lines=("[/d.example/]9.9.9.9",))


def moves_random(world, rnd, people, small=False):
    """A random state of the groups, choices, whole-traffic people and rules (the review's fuzz, r4/fuzz.py, with the
    children's lists added after it, so that the clients some list names are moved too); `small`: one change of it."""
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
        for record in records:
            if rnd.random() < 0.1:
                record["kids_lists"] = rnd.sample(MOVES_KIDS_LISTS, rnd.randint(0, 2))
        return
    homes = {person.name: rnd.choice([None, *MOVES_GROUPS]) for person in people}
    world["groups"].records = [{"id": group, "name": group, "filter": rnd.choice(MOVES_FILTERS), "members": [name for name, home in homes.items() if home == group]}
                               for group in MOVES_GROUPS]
    world["own"].choices = {person.name: rnd.choice(MOVES_FILTERS) for person in people if rnd.random() < 0.3}
    world["whole"].names = {person.name for person in people if rnd.random() < 0.25}
    world["dns"].routes = {group: moves_route(rnd, group) for group in MOVES_GROUPS if rnd.random() < 0.4}
    for record in world["groups"].records:
        record["kids_lists"] = rnd.sample(MOVES_KIDS_LISTS, rnd.randint(0, 2))


def moves_fuzz(seed, trials, people):
    """The review's fuzz (r4/fuzz.py) as a check: chains of random changes, each stopped at a random call or failing
    at random, sometimes followed at once by a small change; after every call the invariant, and in the end three
    undisturbed changes settle. → (changes, calls, broken, parked, not settled)."""
    import random
    rnd = random.Random(seed)
    changes = 0
    calls = 0
    broken = []
    parked = []
    unsettled = []
    for trial in range(trials):
        world = moves_world([], people)
        adguard = world["adguard"]
        adguard.rnd = rnd
        moves_random(world, rnd, people)
        moves_sync(world)
        for _ in range(rnd.randint(2, 6)):
            moves_random(world, rnd, people, small=rnd.random() < 0.6)
            mode = rnd.random()
            adguard.refuse_at = None
            adguard.lose_at = None
            adguard.failing = 0.0
            if mode < 0.4:
                setattr(adguard, rnd.choice(["refuse_at", "lose_at"]), len(adguard.calls) + rnd.randint(0, 6))
            elif mode < 0.7:
                adguard.failing = rnd.choice([0.1, 0.3])
            moves_watch(world, broken, parked)
            before = len(adguard.calls)
            moves_sync(world)
            calls += len(adguard.calls) - before
            changes += 1
            adguard.refuse_at = None
            adguard.lose_at = None
            adguard.failing = 0.0
            if rnd.random() < 0.5:
                moves_random(world, rnd, people, small=True)
                if not moves_settles(world, broken, parked):
                    unsettled.append((seed, trial, json.loads(json.dumps(adguard.have))))
                    break
        if not moves_settles(world, broken, parked):
            unsettled.append((seed, trial, json.loads(json.dumps(adguard.have))))
    return changes, calls, broken, parked, unsettled


def moves_dirty_stops(seed, pairs, people):
    """From dirty starts: a random change stopped at a random call (refused, or answered and lost), then another
    change stopped at every one of its calls in turn, both ways; after every call the invariant, then three undisturbed
    changes settle. → (stop positions, broken, parked, not settled)."""
    import random
    rnd = random.Random(seed)
    positions = 0
    broken = []
    parked = []
    unsettled = []
    for _ in range(pairs):
        origin = moves_world([], people)
        origin["adguard"].rnd = rnd
        moves_random(origin, rnd, people)
        moves_sync(origin)
        moves_random(origin, rnd, people, small=rnd.random() < 0.5)
        setattr(origin["adguard"], rnd.choice(["refuse_at", "lose_at"]), len(origin["adguard"].calls) + rnd.randint(0, 8))
        moves_sync(origin)
        origin["adguard"].refuse_at = None
        origin["adguard"].lose_at = None
        moves_random(origin, rnd, people, small=rnd.random() < 0.5)
        probe = moves_copy(origin)
        probe["adguard"].calls.clear()
        moves_sync(probe)
        for number in range(len(probe["adguard"].calls) + 1):
            for how in ("refuse_at", "lose_at"):
                positions += 1
                world = moves_copy(origin)
                moves_watch(world, broken, parked)
                setattr(world["adguard"], how, number)
                moves_sync(world)
                if not moves_settles(world, broken, parked):
                    unsettled.append((json.loads(json.dumps(origin["adguard"].have)), how, number, world["adguard"].calls))
    return positions, broken, parked, unsettled


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
        parked = [entry for result in mine for entry in result[5]]
        if name == "listed cycle":
            ok(total > 0 and all(not result[3] for result in mine) and all(result[4] for result in mine) and parked,
               f"{name}: обе группы «pro» с правилами меняют список и меняются людьми (у каждого из двух клиентов — список «pro» по имени): каждый из {total} вызовов "
               "отказан, затем без ответа — настройки у всех всё время прежние или нужные; списка по имени на миг нет только у тех, кого держит временный клиент "
               "(иначе никак: обойти нельзя), и об этом — в отчёте", [result for result in mine if result[3] or not result[4]][:1])
            continue
        ok(total > 0 and all(not result[3] for result in mine) and all(result[4] for result in mine) and not parked,
           f"{name}: каждый из {total} вызовов отказан, затем выполнен с потерянным ответом — после каждого вызова каждый отфильтрован ровно как был или как надо "
           "(все настройки и списки по имени клиента); три следующих спокойных раза — ровно как надо, потом ни одного вызова",
           [result for result in mine if result[3] or not result[4] or result[5]][:1])

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
                    return None if holder is None else (holder, settings_of(as_profile(holder, adguard.have[holder])))
                was = {address: key(address) for address in addresses}

                def seen(call, adguard=adguard, was=was, key=key):
                    for address in addresses:
                        wanted = wanted_at.get(address)
                        now = key(address)
                        if now is not None and was[address] is not None and now[0].startswith(MOVE_PROFILE) and now[1] == was[address][1]:
                            continue        # a stand-in no list names (none does here): exactly what they had
                        if now not in (was[address], None if wanted is None else (wanted.name, settings_of(wanted))):
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
                                                                [KnownUser(name="bob", ip="10.9.0.6"), KnownUser(name="ann", ip="10.9.0.7"), KnownUser(name="eve", ip="10.9.0.8")],
                                                                whole={"eve"})}
    sent = {client["name"]: client for client in stack_adguard.AGENT_CLIENTS}
    ok(set(sent) <= set(made) and all({**made[name], "upstreams": None} == {**client, "upstreams": None} for name, client in sent.items()),
       "tests/stack_adguard.py читает назад клиентов ровно таких, какими их шлёт агент (детский и «-all» уровня; кроме самих адресов резолверов)", [made, sent])

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
    for seed in (3, 11, 12):
        changes, calls, broken, parked, unsettled = moves_fuzz(seed, fuzz_trials, people)
        ok(not broken and not unsettled,
           f"fuzz ревью, seed {seed}: {fuzz_trials} цепочек, {changes} изменений, {calls} вызовов, часть отказана или без ответа, после остановки — новая правка: "
           f"каждый всё время как был или как надо; три спокойных раза — ровно как надо, потом ни одного вызова (временный клиент без списка по имени: {len(parked)} раз)",
           [broken[:1], unsettled[:1]])
    pairs = int(os.environ.get("MOVES_DIRTY_PAIRS", "120"))
    positions, broken, parked, unsettled = moves_dirty_stops(20261123, pairs, people)
    ok(not broken and not unsettled,
       f"с грязного старта (прошлое изменение остановлено на случайном вызове): {pairs} пар изменений, остановка на каждом из вызовов — {positions} положений: "
       f"каждый всё время как был или как надо; затем ровно как надо и ни одного вызова (временный клиент без списка по имени: {len(parked)} раз)", [broken[:1], unsettled[:1]])


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
    t_profiles()
    t_profiles_routes()
    t_client_moves()
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
