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
       and not [call for call in clients.calls if call[0] in ("delete", "add")] and clients.calls[0][0] == "update" and clients.calls[0][1] == route_variant("grp-kkkkk1", "kkkkk1"),
       "человек перешёл из группы в группу: сначала клиент, что его отдаёт, потом тот, что берёт — оба на месте", clients.calls)
    huge = tuple(f"[/site{number}.example.net/]1.1.1.1 9.9.9.9" for number in range(1800))
    clients.calls.clear()
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8", *huge), direct_lines=()), "hhhhh1": dns.routes["hhhhh1"]}
    ok(sync() is None and route_variant("grp-kkkkk1", "kkkkk1") not in clients.have and clients.have.get("grp-kkkkk1", {}).get("upstreams") == []
       and memory.degraded == frozenset({"kkkkk1"}) and "grp-rhhhhh1" in clients.have and not [call for call in clients.calls if call[0] in ("add", "delete")],
       f"список детей не помещается в один запрос (> {CLIENT_REQUEST_BYTES} байт с адресами): их клиент спрашивает как все — тот же клиент, переименованный на месте (детская фильтрация ни на миг не снята)",
       [sorted(clients.have), clients.calls])
    ok(Lists.routed == ["hhhhh1", "kkkkk1"], "списки групп по-прежнему называют её варианты: вернётся — они уже на месте", Lists.routed)
    said = [line for line in journal.lines if "не помещается" in line["text"]]
    sync()
    ok(len(said) == 1 and said[0]["bad"] and "«Дети»" in said[0]["text"] and len([line for line in journal.lines if "не помещается" in line["text"]]) == 1,
       "в журнале — один раз, а не с каждым опросом", journal.lines)
    dns.routes = {"kkkkk1": RouteDns(upstreams=("8.8.8.8",), direct_lines=()), "hhhhh1": dns.routes["hhhhh1"]}
    clients.calls.clear()
    ok(sync() is None and route_variant("grp-kkkkk1", "kkkkk1") in clients.have and memory.degraded == frozenset() and not [call for call in clients.calls if call[0] in ("add", "delete")]
       and any("снова" in line["text"] for line in journal.lines), "список снова помещается: клиент группы вернулся сам, в журнале — что вернулся", sorted(clients.have))
    many = [KnownUser(name=f"u{number}", ip=f"10.{number // 250 + 10}.{number % 250}.2") for number in range(2600)]
    crowd = dict(home, members=[user.name for user in many])
    dns.routes = {"hhhhh1": RouteDns(upstreams=("8.8.8.8", *huge[:900]), direct_lines=())}
    fit_alone = run(profiles.sync([UserGroupOf(crowd)], many))
    ok(fit_alone is None and "grp-rhhhhh1" not in clients.have and memory.degraded == frozenset({"hhhhh1"}),
       "группа выросла: список влезал, а с адресами её людей — нет; её люди — как все, и блокировщик ничего не отверг", [fit_alone, sorted(clients.have)])


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
