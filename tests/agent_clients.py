#!/usr/bin/env python3
"""The agent's side of the other services: its typed clients, and what the
commands make of their answers — the shapes the panel and the bot read.

    uv run python tests/agent_clients.py

The services are the real applications of awgscale.users, awgscale.watch,
awgscale.resolver and awgscale.exits — their routes, their request models,
their token check, their ways of saying no — asked in-process; only the
service object behind each is a stand-in that answers with the service's own
models. So a change of a contract on either side fails here. One line per
check, the last line is "N of M checks passed".
"""
import asyncio
import datetime
import json
import os
import sys
import tempfile
from ipaddress import IPv4Address

TMP = tempfile.mkdtemp(prefix="agent-clients-")
TOKEN = os.path.join(TMP, "token")
with open(TOKEN, "w") as f:
    f.write("t0ken\n")
os.environ.update({"USERS_TOKEN_FILE": TOKEN, "WATCH_TOKEN_FILE": TOKEN, "RESOLVER_TOKEN": TOKEN, "EXITS_TOKEN_FILE": TOKEN, "USERS_ENDPOINT": "vpn.example"})

import httpx  # noqa: E402
from pydantic import SecretStr  # noqa: E402

import awgscale.exits.api as exits_api  # noqa: E402
import awgscale.resolver.api as resolver_api  # noqa: E402
import awgscale.users.api as users_api  # noqa: E402
import awgscale.watch.api as watch_api  # noqa: E402
from awgscale.agent.clients.exits import AddBody, ExitsClient  # noqa: E402
from awgscale.agent.clients.http import ServiceClient, ServiceDown, ServiceRefused  # noqa: E402
from awgscale.agent.clients.resolver import ResolverClient  # noqa: E402
from awgscale.agent.clients.users import UsersClient  # noqa: E402
from awgscale.agent.clients.watch import WatchClient  # noqa: E402
from awgscale.agent import errors  # noqa: E402
from awgscale.agent.clients.gates import WatchService  # noqa: E402
from awgscale.agent.exits.gate import ExitsService  # noqa: E402
from awgscale.agent.exits.models import ServersShown  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.sites.gates import KeeperOfResolver, WatchForSites  # noqa: E402
from awgscale.agent.spaces.gates import WatchSpaces  # noqa: E402
from awgscale.agent.users.directory import Directory, UsersService as UsersGateway  # noqa: E402
from awgscale.agent.wire import exit_shape, work_shape  # noqa: E402
from awgscale.exits import models as exits_models  # noqa: E402
from awgscale.exits.app import app as exits_app  # noqa: E402
from awgscale.resolver import models as resolver_models  # noqa: E402
from awgscale.resolver.app import app as resolver_app  # noqa: E402
from awgscale.service.errors import Down, Refused  # noqa: E402
from awgscale.users import models as users_models  # noqa: E402
from awgscale.users.app import app as users_app  # noqa: E402
from awgscale.watch import models as watch_models  # noqa: E402
from awgscale.watch.app import app as watch_app  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = datetime.UTC
MADE = datetime.datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
UNTIL = datetime.datetime(2026, 12, 1, 12, 0, 0, 500000, tzinfo=UTC)
n = failed = 0


def ok(cond, what, got=None):
    global n, failed
    n += 1
    failed += not cond
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or got is None else f"   — got: {got!r}"[:600]))


class Mood:
    """What the stand-in services do next: answer, refuse, or be away."""

    refuse = None
    down = False

    @classmethod
    def act(cls):
        if cls.down:
            raise Down("the database is away")
        if cls.refuse:
            raise Refused(cls.refuse)


class Users:
    seen = []

    async def list(self):
        Mood.act()
        return [users_models.UserView(name="ann", ip=IPv4Address("172.16.17.2"), expires_at=None, created_at=MADE),
                users_models.UserView(name="bob", ip=IPv4Address("172.16.17.3"), expires_at=UNTIL, created_at=MADE)]

    async def add(self, request):
        Mood.act()
        self.seen.append(request)
        return users_models.Created(name=request.name, ip=IPv4Address("172.16.17.4"), expires_at=UNTIL if request.expires else None, warning=None)

    async def remove(self, names):
        Mood.act()
        return [users_models.Removal(name=name, status=users_models.Status.REMOVED if name == "ann" else users_models.Status.NOT_FOUND) for name in names]

    async def set_expiry(self, request):
        Mood.act()
        self.seen.append(request)
        return UNTIL if request.expires else None

    async def config(self, name):
        Mood.act()
        if name != "ann":
            raise Refused("нет такого пользователя")
        return users_models.DevicePayload(name=name, conf="[Interface]\n", uri="vpn://x", conf_png="QQ==", uri_png="QUI=")


def exit_view(index, state, handshake):
    return watch_models.ExitView(index=index, name=f"exit{index}", iface=f"awgk{index}", state=state, handshake=handshake, rx=10, tx=20,
                                 clients=[IPv4Address("172.16.17.2")], probe_ms=12.5 if handshake else None, changed=MADE if handshake else None)


class Watch:
    pinned = True

    def health(self):
        Mood.act()
        return watch_models.Health(started=MADE, applied=None, last_tick=MADE)

    async def status(self):
        Mood.act()
        work = watch_models.Work(started=MADE, applied=MADE, last_tick=None, tick_seconds=0.25, ticks=7, tick_failed=1)
        return watch_models.Status(time=MADE, exits=[exit_view(1, "up", MADE), exit_view(3, "unknown", None)], udp_via_exit=True, route_mode="list",
                                   force_exit_nets=11, ru_networks=8664, force_exit_ips=5, force_direct_ips=0, watch=work)

    async def apply(self):
        Mood.act()
        return watch_models.Applied(mode="geo")

    async def pin(self):
        Mood.act()
        return watch_models.Pinned(ok=self.pinned)

    async def spaces(self):
        Mood.act()
        return watch_models.SpacesLoaded(spaces=2)

    async def domains(self):
        Mood.act()
        return watch_models.DomainsReset(nets=11)


class Keeper:
    fail = None

    async def health(self):
        return resolver_models.Health(running=True, listening=True, wanted=True, started=MADE, starts=3, exits=1, last_exit=-15)

    async def stop(self):
        if self.fail:
            raise resolver_models.NotDone(self.fail)

    async def start(self):
        if self.fail:
            raise resolver_models.NotDone(self.fail)


class Exits:
    seen = []

    async def nodes(self):
        Mood.act()
        node = exits_models.Node(address=IPv4Address("198.51.100.38"), managed=True, port=47211, added=MADE, updated=None)
        job = exits_models.Job(id="j1", kind="add", index=4, name="exit4-de", address=IPv4Address("198.51.100.40"), state="done", started=MADE, finished=UNTIL,
                               steps=[exits_models.Step(key="login", title="Вход на сервер", state="done")], weight=25)
        return {"ok": True, "nodes": {"1": node.wire()}, "job": job.wire(), "busy": False, "key": "ssh-ed25519 AAAA entry"}

    async def add(self, request):
        Mood.act()
        self.seen.append(request)
        return {"ok": True, "started": True}

    async def update(self, request):
        Mood.act()
        self.seen.append(request)
        return {"ok": True, "started": True}

    async def remove(self, request):
        Mood.act()
        self.seen.append(request)
        return {"ok": True, "cleaning": True}


users_app.dependency_overrides[users_api.get_service] = Users
watch_app.dependency_overrides[watch_api.get_service] = Watch
resolver_app.dependency_overrides[resolver_api.get_keeper] = Keeper
exits_app.dependency_overrides[exits_api.get_service] = Exits


def client(app, token_file=TOKEN):
    return ServiceClient("http://service", __import__("pathlib").Path(token_file), httpx.ASGITransport(app=app))


class Unreachable(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise httpx.ConnectError("connection refused")


def nobody():
    return ServiceClient("http://service", __import__("pathlib").Path(TOKEN), Unreachable())


def raised(work):
    try:
        asyncio.run(work)
    except ServiceRefused as e:
        return "refused", str(e)
    except ServiceDown as e:
        return "down", str(e), e.status
    return None,


def t_clients():
    print("\n— клиенты: ответы сервисов как модели")
    users = UsersClient(client(users_app))
    listed = asyncio.run(users.list())
    ok([u.name for u in listed] == ["ann", "bob"] and listed[1].expires_at == UNTIL and listed[0].expires_at is None and listed[0].created_at == MADE and listed[0].ip == IPv4Address("172.16.17.2"),
       "пользователи: список приходит моделями, время — датой со временем", listed)
    made = asyncio.run(users.add("carl", "7d"))
    ok(made.status is users_models.Status.CREATED and made.expires_at == UNTIL and Users.seen[-1].name == "carl" and Users.seen[-1].expires.count == 7, "создание: сервис получил имя и срок по своей модели, ответ — модель", made)
    ok(raised(users.add("-bad", None))[0] == "refused" and "имя: латиница" in raised(users.add("-bad", None))[1], "тело не по модели (422) — отказ со словами сервиса из его ответа", raised(users.add("-bad", None)))
    ok(raised(users.add("carl", "7 days")) == ("refused", "срок: например 12h, 7d, 4w"), "негодный срок (422) — отказ с правилом срока", raised(users.add("carl", "7 days")))
    Mood.refuse = "пользователь с таким именем уже есть"
    ok(raised(users.add("carl", None)) == ("refused", "пользователь с таким именем уже есть"), "отказ сервиса (400) — те же слова", raised(users.add("carl", None)))
    Mood.refuse = None
    removed = asyncio.run(users.remove(["ann", "nobody"]))
    ok(removed.ok is False and [r.status.value for r in removed.results] == ["removed", "not_found"], "удаление: общий итог и судьба каждого имени", removed)
    ok(asyncio.run(users.config("ann")).conf.startswith("[Interface]") and asyncio.run(users.config("nobody")) is None, "конфиг устройства; нет такого пользователя — None")
    Mood.down = True
    ok(raised(users.list())[0] == "down" and raised(users.list())[2] == 503, "сервис не может выполнить (503) — недоступен, а не пустой список", raised(users.list()))
    Mood.down = False
    ok(raised(UsersClient(nobody()).list())[0] == "down" and raised(UsersClient(nobody()).list())[2] is None, "сервис не отвечает вовсе — недоступен, без кода ответа")
    ok(raised(UsersClient(client(users_app, os.path.join(TMP, "no-token"))).list())[0] == "down", "нет файла с токеном — недоступен")
    wrong = os.path.join(TMP, "wrong")
    open(wrong, "w").write("other\n")
    ok(raised(UsersClient(client(users_app, wrong)).list())[:1] == ("down",) and raised(UsersClient(client(users_app, wrong)).list())[2] == 401, "чужой токен (401) — недоступен")

    watch = WatchClient(client(watch_app))
    status = asyncio.run(watch.status())
    ok(status.udp_via_exit is True and status.exits[0].handshake == MADE and status.exits[1].handshake is None and status.exits[1].state.value == "unknown" and status.watch.ticks == 7,
       "сторож: состояние приходит моделью (время датой, UDP-флаг булевым)", status)
    ok(asyncio.run(watch.pin()).ok is True and asyncio.run(watch.apply()).mode.value == "geo" and asyncio.run(watch.spaces()).spaces == 2 and asyncio.run(watch.domains()).nets == 11 and asyncio.run(watch.health()).applied is None,
       "сторож: применить, закрепить, пространства, домены, здоровье")
    Mood.refuse = "нет ни одного выхода"
    ok(raised(watch.apply()) == ("refused", "нет ни одного выхода"), "отказ сторожа теперь 400 (был 409) — те же слова", raised(watch.apply()))
    Mood.refuse = None

    keeper = ResolverClient(client(resolver_app))
    ok(asyncio.run(keeper.health()).started == MADE and asyncio.run(keeper.stop()).ok and asyncio.run(keeper.start()).ok, "блокировщик: здоровье, стоп, старт")
    Keeper.fail = "блокировщик не остановился"
    ok(raised(keeper.stop()) == ("down", "блокировщик не остановился", 500), "блокировщик не смог (500) — причина его словами", raised(keeper.stop()))
    Keeper.fail = None

    exits = ExitsClient(client(exits_app))
    registry = asyncio.run(exits.registry())
    ok(registry.nodes["1"].added == MADE and registry.job.finished == UNTIL and registry.job.steps[0].state.value == "done" and registry.key.startswith("ssh-ed25519"), "выходы: реестр и задание приходят моделями", registry)
    body = AddBody(index=4, name="exit4-de", weight=25, address="198.51.100.40", ssh_port=22, password=SecretStr("r00t-pass"))
    asyncio.run(exits.add(body))
    ok(Exits.seen[-1].password.get_secret_value() == "r00t-pass" and Exits.seen[-1].index == 4 and "r00t-pass" not in repr(body) and "r00t-pass" not in str(body.model_dump()),
       "пароль root доходит до сервиса выходов и нигде больше не виден: ни в repr запроса, ни в его выгрузке", repr(body))
    bad = raised(exits.add(AddBody(index=4, name="exit4 de", weight=25, address="198.51.100.40", ssh_port=22, password=SecretStr("r00t-pass"))))
    ok(bad[0] == "refused" and "имя выхода" in bad[1] and "r00t-pass" not in bad[1], "запрос не по модели (422) — отказ со словами сервиса, пароля в нём нет", bad)


def t_answer_shapes():
    print("\n— что команды делают из ответов сервисов: те же виды, что и раньше")
    run = asyncio.run
    path = __import__("pathlib").Path
    cache = path(TMP) / "clients.json"
    users = UsersGateway(UsersClient(client(users_app)))
    directory = Directory(UsersClient(client(users_app)), cache, path(TMP) / "awg0.conf")
    listed = run(directory.list(force=True))
    held = [{"name": "ann", "ip": "172.16.17.2", "expires_at": None, "created_at": int(MADE.timestamp())},
            {"name": "bob", "ip": "172.16.17.3", "expires_at": int(UNTIL.timestamp()), "created_at": int(MADE.timestamp())}]
    ok(json.load(open(cache)) == held, "пользователи: адрес строкой, время — целыми секундами Unix (как читают панель и бот)", json.load(open(cache)))
    ok([(u.name, u.ip, u.expires_at, u.created_at) for u in listed] == [("ann", "172.16.17.2", None, MADE), ("bob", "172.16.17.3", UNTIL, MADE)] and [(u.name, u.ip, u.expires_at) for u in run(directory.list())] == [("ann", "172.16.17.2", None), ("bob", "172.16.17.3", UNTIL.replace(microsecond=0))], "тот же список ложится в кэш агента")
    r = run(users.add("carl", "7d"))
    ok(r.ok and r.answer == {"ok": True, "results": [{"name": "carl", "status": "created", "ip": "172.16.17.4", "expires_at": int(UNTIL.timestamp())}]}, "создание: прежний вид ответа", r)
    ok(run(users.add("dan", None)).answer["results"][0]["expires_at"] is None and "warning" not in run(users.add("dan", None)).answer["results"][0], "без срока — null, предупреждения нет, пока его нет")
    Mood.refuse = "пользователь с таким именем уже есть"
    ok(run(users.add("carl", None)).answer == {"ok": False, "error": "пользователь с таким именем уже есть"} and run(users.set_expiry(["carl"], "7d")) == Outcome(False, "пользователь с таким именем уже есть"), "отказ — прежний вид, слова сервиса")
    Mood.refuse = None
    ok(run(users.add("-bad", None)).answer == {"ok": False, "error": "имя: латиница, цифры, _ и -, до 32 символов"}, "422 сервиса агент отдаёт как прежний отказ с понятными словами", run(users.add("-bad", None)))
    removed = run(users.remove(["ann", "nobody"]))
    ok(removed.answer == {"ok": False, "results": [{"name": "ann", "status": "removed"}, {"name": "nobody", "status": "not_found"}]} and [one.status for one in removed.results] == ["removed", "not_found"], "удаление: прежний вид", removed)
    ok(run(users.set_expiry(["ann"], None)) == Outcome(True) and Users.seen[-1].expires is None and run(users.set_expiry(["ann"], "12h")).ok is True and Users.seen[-1].expires.count == 12, "срок: «none» уходит сервису как отсутствие срока")
    config = run(users.config("ann"))
    ok(config == {"ok": True, "name": "ann", "conf": "[Interface]\n", "uri": "vpn://x", "conf_png": "QQ==", "uri_png": "QUI="} and run(users.config("nobody")) is None, "конфиг устройства — прежний вид; нет пользователя — None", config)
    Mood.down = True
    down = raised(UsersClient(client(users_app)).list())
    ok(down[0] == "down" and run(users.add("eve", None)) is None and run(users.remove(["ann"])) is None and [u.name for u in run(directory.list(force=True))] == ["ann", "bob"],
       "сервис недоступен — это не отказ и не «никого»: список берётся последний известный, не пустой", down)
    Mood.down = False

    watch = WatchService(WatchClient(client(watch_app)))
    status = run(watch.status(20))
    ok(status is not None and status.time == MADE and status.udp_via_exit is True and status.route_mode.value == "list" and status.ru_networks == 8664,
       "сторож: его ответ доходит до команд целиком — время, UDP-флаг, режим, счётчики", status)
    shown = [exit_shape(view) for view in status.exits]
    ok(shown[0] == {"index": 1, "name": "exit1", "iface": "awgk1", "state": "up", "handshake": int(MADE.timestamp()), "rx": 10, "tx": 20, "clients": ["172.16.17.2"], "probe_ms": 12.5, "changed": MADE.timestamp()}
       and shown[1]["handshake"] == 0 and shown[1]["changed"] is None and shown[1]["state"] == "unknown",
       "выходы: рукопожатие секундами (0 — не было), адреса строками", shown)
    ok(work_shape(status.watch) == {"started": MADE.timestamp(), "applied": MADE.timestamp(), "last_tick": 0, "tick_seconds": 0.25, "ticks": 7, "tick_failed": 1}, "работа сторожа: моменты числами, «никогда» — 0", work_shape(status.watch))
    for_sites = WatchForSites(WatchClient(client(watch_app)))
    for_spaces = WatchSpaces(WatchClient(client(watch_app)))
    ok(run(watch.health(10)).ok and run(watch.apply(60)).ok and run(watch.pin(60)).ok and run(for_spaces.load(60)).ok and run(for_sites.domains(60)).ok, "остальные команды сторожа: сделано")
    Watch.pinned = False
    ok(run(watch.pin(60)).ok is False, "закрепление пропущено (ok=false) — не сделано, как раньше")
    Watch.pinned = True
    Mood.refuse = "нет ни одного выхода"
    ok(run(watch.apply(60)) == Outcome(False, "нет ни одного выхода"), "отказ сторожа — не сделано, и его слова", run(watch.apply(60)))
    Mood.refuse = None
    Mood.down = True
    ok(run(watch.apply(60)) == Outcome(False, "сервис сейчас не может выполнить запрос") and run(watch.status(20)) is None, "сторож не может — его же слова; о выходах тогда ничего не известно (а не «их нет»)", run(watch.apply(60)))
    Mood.down = False

    keeper = KeeperOfResolver(ResolverClient(client(resolver_app)))
    health = run(ResolverClient(client(resolver_app)).health())
    ok(run(keeper.stop(60)).ok and run(keeper.start(60)).ok and health.listening is True and health.started == MADE, "блокировщик: стоп, старт, здоровье (момент запуска)", health)
    Keeper.fail = "блокировщик не остановился"
    ok(run(keeper.stop(60)) == Outcome(False, "блокировщик не остановился"), "блокировщик не смог — причина его словами", run(keeper.stop(60)))
    Keeper.fail = None

    exits = ExitsService(ExitsClient(client(exits_app)))
    nodes = ServersShown(registry=run(exits.registry())).wire()
    ok(nodes["ok"] and nodes["nodes"]["1"]["added"] == int(MADE.timestamp()) and "updated" not in nodes["nodes"]["1"] and nodes["nodes"]["1"]["address"] == "198.51.100.38" and nodes["nodes"]["1"]["managed"] is True,
       "выходы: сервер в прежнем виде, «когда добавлен» — секундами", nodes["nodes"])
    job = nodes["job"]
    ok(job["started"] == int(MADE.timestamp()) and job["finished"] == int(UNTIL.timestamp()) and job["state"] == "done" and job["kind"] == "add" and job["steps"] == [{"key": "login", "title": "Вход на сервер", "state": "done"}] and job["weight"] == 25 and "reboot" not in job,
       "задание: начало и конец секундами (страница считает по ним давность), шаги как были", job)
    sent = run(exits.add(4, "exit4-de", 25, "198.51.100.40", 22, SecretStr("r00t-pass")))
    ok(sent == Outcome(True) and Exits.seen[-1].password.get_secret_value() == "r00t-pass", "добавление выхода: пароль доходит до сервиса", sent)
    ok(run(exits.update(1, True, "exit1-nl")) == Outcome(True) and Exits.seen[-1].reboot is True and run(exits.remove(1, "awgk1", "exit1-nl")) == Outcome(True) and Exits.seen[-1].iface == "awgk1", "обновление и удаление выхода")
    Mood.refuse = "идёт другое задание"
    ok(run(exits.update(1, False, "x")) == Outcome(False, "идёт другое задание"), "отказ сервиса выходов — прежний вид")
    Mood.refuse = None
    Mood.down = True
    cannot = refusal_of(exits.registry())
    ok(cannot == "сервис выходов сейчас не может выполнить запрос" and run(exits.update(1, False, "x")).said == cannot, "сервис выходов не может — прежние слова", cannot)
    Mood.down = False
    ok(refusal_of(ExitsService(ExitsClient(nobody())).registry()) == "сервис выходов не отвечает" and run(WatchService(WatchClient(nobody())).health(10)).said.startswith("сторож не отвечает: ")
       and run(KeeperOfResolver(ResolverClient(nobody())).stop(60)).said.startswith("блокировщик не отвечает: "), "никто не отвечает — прежние слова для каждого сервиса")


def refusal_of(work):
    """The words a command would refuse with."""
    try:
        asyncio.run(work)
    except errors.Refused as e:
        return e.error
    return None


def main():
    t_clients()
    t_answer_shapes()
    print(f"\n{n - failed} of {n} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
