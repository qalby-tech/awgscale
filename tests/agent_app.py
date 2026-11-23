#!/usr/bin/env python3
"""The app's side of the agent: the owner's code (owner-code, then invite-redeem
with it), and app-me, app-exits, app-exit — through the real dispatcher, with
stand-ins for the store, the users service and the watch service.

    uv run python tests/agent_app.py

Nothing live is touched."""
import asyncio
import json
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from awgscale.agent.app.owners import Owner, is_owner, owners_of
from awgscale.agent.app.service import AppService
from awgscale.agent.cascade.models import CascadeSettings, ExitSetting
from awgscale.agent.commands import Command
from awgscale.agent.dispatcher import Dispatcher
from awgscale.agent.handling import Handled
from awgscale.agent.invites.handling import HandledFor
from awgscale.agent.invites.notes import JournalNotes
from awgscale.agent.invites.service import InvitesService
from awgscale.agent.invites.store import unexpired
from awgscale.agent.journal.service import Journal
from awgscale.agent.ports import Outcome
from awgscale.agent.protocol import Caller
from awgscale.agent.shell import OwnerCodeRequest, parse_shell
from awgscale.agent.spaces.models import StoredSpaces
from awgscale.agent.state.models import ExitFigures
from awgscale.agent.users.directory import user_of
from awgscale.agent.users.models import ExitChosen, StoredGroups

LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
TMP = tempfile.mkdtemp(prefix="agent-app-")
MADE = 1763300000


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else f"   — got: {seen!r}"[:600])


def copy(value):
    return json.loads(json.dumps(value))


class World:
    def __init__(self):
        self.clients = [
            {"name": "alice", "ip": "172.16.17.2", "expires_at": None, "created_at": MADE},
            {"name": "bob", "ip": "172.16.17.3", "expires_at": MADE + 86400, "created_at": MADE},
            {"name": "carl", "ip": "172.16.17.4", "expires_at": None, "created_at": MADE},
        ]
        self.groups = [
            {"id": "aaaaaa", "name": "Дом", "space": True, "exit": 3, "filter": "kids", "members": ["alice", "bob"]},
            {"id": "bbbbbb", "name": "Гости", "space": False, "exit": None, "filter": "std", "members": ["carl"]},
        ]
        self.invites = []
        self.owners = None
        self.pins = {}
        self.own = {}
        self.whole = set()
        self.compiled = []
        self.synced = []
        self.profiles_fail = None
        self.lines = []
        self.chosen = []
        self.up = {1, 3}
        # the last look at the state: exit 3's link was not seen there (asked of the tunnel), exit 4 points nowhere
        self.figures = {1: ExitFigures(address="198.51.100.38", users=17, online=4), 3: ExitFigures(address=None, users=2, online=0)}
        self.look_fails = False
        self.endpoints = {"awgk3": "203.0.113.20:51820"}
        self.exits = (ExitSetting(index=1, iface="awgk1", tip="10.77.1.2", name="exit1-nl", weight=2, country="nl"),
                      ExitSetting(index=3, iface="awgk3", tip="10.77.3.2", name="exit3-de", weight=1),
                      ExitSetting(index=4, iface="awgk4", tip="10.77.4.2", name="exit4-fi", weight=1))

    def journal(self):
        return [json.loads(line)["text"] for line in self.lines]


def services(w):
    class Dir:
        async def list(self, force=False):
            return [user_of(c) for c in w.clients]

    class Invites:
        async def read(self):
            return unexpired(copy(w.invites), datetime.fromtimestamp(int(time.time()), UTC))

        async def save(self, invitations):
            w.invites = [invitation.record() for invitation in invitations]

    class Groups:
        async def read(self):
            return StoredGroups.of(copy(w.groups))

        async def save(self, records):
            w.groups = copy(list(records))

    class Spaces:
        async def read(self):
            return StoredSpaces.of([])

        async def save(self, records):
            pass

    class SpaceRules:
        async def compile(self, spaces, groups=None):
            return None

    class Users:
        async def set_expiry(self, names, term):
            return Outcome(True)

        async def config(self, name):
            return {"ok": True, "name": name, "conf": "[Interface]\n", "uri": "vpn://x", "conf_png": "AA", "uri_png": "BB"}

    class Newcomers:
        async def ready(self):
            pass

        async def forget(self, name):
            pass

        async def add(self, name):
            w.clients.append({"name": name, "ip": f"172.16.17.{len(w.clients) + 2}", "expires_at": None, "created_at": MADE + len(w.clients)})
            return True

        async def pin(self, groups, users):
            pass

        async def filter(self, groups, users):
            pass

    class Watch:
        async def pin(self, timeout):
            return Outcome(True)

    class Owners:
        async def read(self):
            return owners_of(w.owners)

        async def add(self, user):
            owners = [owner for owner in owners_of(w.owners) if owner.name != user.name] + [Owner(name=user.name, made=user.created_at)]
            w.owners = json.dumps([owner.model_dump(mode="json") for owner in owners])

    class HandPins:
        async def read(self):
            return dict(w.pins)

        async def save(self, pins):
            w.pins = dict(pins)

        async def whole(self):
            return set(w.whole)

        async def save_whole(self, names):
            w.whole = set(names)

    class Pinning:
        async def compile(self, groups, users):
            w.compiled.append(sorted(w.whole))
            return True

    class OwnFilters:
        async def read(self):
            return dict(w.own)

        async def save(self, chosen):
            w.own = dict(chosen)

    class Profiles:
        async def sync(self, groups, users):
            w.synced.append(dict(w.own))
            return w.profiles_fail

    class Cascade:
        async def load(self):
            return CascadeSettings.model_construct(exits=w.exits)

    class Working:
        def up(self):
            return set(w.up)

    class Seen:
        async def exit_figures(self):
            if w.look_fails:
                raise RuntimeError("состояние ещё не получено")
            return dict(w.figures)

    class Tunnel:
        async def endpoint_of(self, iface):
            return w.endpoints.get(iface)

    class UsersService:
        async def choose_exit(self, request):
            w.chosen.append((request.exit, list(request.names)))
            index = None if request.exit == "auto" else int(request.exit)
            return ExitChosen(names=list(request.names), exit=index, exit_name={exit_.index: exit_.name for exit_ in w.exits}.get(index))

    class Writer:
        async def write(self, entry):
            w.lines.append(entry.line())

    journal = Journal(None, Writer())
    invites = InvitesService(Invites(), Groups(), Dir(), Users(), Newcomers(), Watch(), Spaces(), SpaceRules(), JournalNotes(journal),
                             Path(TMP) / ".users.lock", Path(TMP) / ".invite.lock", owners=Owners())
    app = AppService(Dir(), Groups(), HandPins(), Owners(), Cascade(), Working(), UsersService(), OwnFilters(), Profiles(), Path(TMP) / ".users.lock", Pinning(), Seen(), Tunnel())
    dispatcher = Dispatcher()
    dispatcher.register(Command.INVITE_LIST, Handled(invites.list))
    dispatcher.register(Command.INVITE_ADD, HandledFor(invites.add))
    dispatcher.register(Command.INVITE_INFO, Handled(invites.info))
    dispatcher.register(Command.INVITE_REDEEM, Handled(invites.redeem))
    dispatcher.register(Command.APP_ME, Handled(app.me))
    dispatcher.register(Command.APP_EXITS, Handled(app.exits))
    dispatcher.register(Command.APP_EXIT, Handled(app.choose, journal))
    dispatcher.register(Command.APP_FILTER, Handled(app.choose_filter, journal))
    dispatcher.register(Command.APP_ROUTE, Handled(app.choose_route, journal))
    return invites, dispatcher


def run(work):
    return LOOP.run_until_complete(work)


def owner_code(invites, *arguments):
    request = parse_shell(["owner-code", *arguments])
    assert isinstance(request, OwnerCodeRequest)
    from awgscale.agent.handling import answered
    return run(answered(invites.owner_code(request)))


def t_owner_code():
    print("\n— код владельца")
    w = World()
    invites, dispatcher = services(w)
    ask = lambda *arguments: run(dispatcher.dispatch(list(arguments), Caller.PANEL))  # noqa: E731
    ok(parse_shell(["owner-code"]) is not None and parse_shell(["owner-code", "if-none"]) is not None and parse_shell(["owner-code", "x"]) is None,
       "owner-code: без аргументов или с if-none")
    first = owner_code(invites, "if-none")
    ok(first.get("ok") is True and len(first.get("token", "")) == 22 and first.get("owners") == [], "сеть без владельца: код выдан", first)
    left = first.get("expires_at", 0) - time.time()
    ok(14 * 60 < left <= 15 * 60, "код действует 15 минут", left)
    ok(len(w.invites) == 1 and w.invites[0].get("owner") is True and w.invites[0]["left"] == 1 and first["token"] not in json.dumps(w.invites),
       "хранится одно приглашение владельца на одно устройство, без самой ссылки", w.invites)
    listed = ask("invite-list")
    ok(listed.get("ok") and listed.get("invites") == [], "в списке приглашений панели кода владельца нет", listed)
    second = owner_code(invites)
    ok(second.get("ok") and second["token"] != first["token"] and len(w.invites) == 1, "новый код заменяет неиспользованный старый", w.invites)
    stale = ask("invite-info", first["token"])
    ok(stale.get("ok") is False, "старый код больше не открывается", stale)
    redeemed = ask("invite-redeem", second["token"], "owner-phone")
    ok(redeemed.get("ok") and redeemed.get("owner") is True and redeemed.get("uri") == "vpn://x", "по коду создан пользователь, и он владелец", redeemed)
    ok(any(text == "По коду владельца создан пользователь owner-phone: владелец сети" for text in w.journal()), "в журнале — кто стал владельцем", w.journal())
    stored = owners_of(w.owners)
    ok([owner.name for owner in stored] == ["owner-phone"] and stored[0].made is not None, "владелец записан с моментом, когда создан пользователь", w.owners)
    again = ask("invite-redeem", second["token"], "other-phone")
    ok(again.get("ok") is False, "код на одно устройство: второй раз не работает", again)
    already = owner_code(invites, "if-none")
    ok(already.get("ok") is False and already.get("already") is True and len(w.invites) == 1, "владелец уже есть: с if-none код не выдан и ничего не сохранено", already)
    third = owner_code(invites)
    ok(third.get("ok") and third.get("owners") == ["owner-phone"], "без if-none — выдан, и сказано, кто уже владелец", third)
    plain = ask("invite-add", "eyJncm91cCI6IG51bGwsICJ1c2VzIjogMSwgImRheXMiOiAxfQ==")
    guest = ask("invite-redeem", plain.get("token", ""), "guest-phone")
    ok(guest.get("ok") and "owner" not in guest, "обычное приглашение владельцем не делает", guest)
    ok([owner.name for owner in owners_of(w.owners)] == ["owner-phone"], "владельцев по-прежнему один", w.owners)


def t_owners():
    print("\n— владелец — тот самый пользователь")
    made = datetime.fromtimestamp(MADE, UTC)
    alice = user_of({"name": "alice", "ip": "172.16.17.2", "created_at": MADE})
    again = user_of({"name": "alice", "ip": "172.16.17.9", "created_at": MADE + 99})
    ok(is_owner([Owner(name="alice", made=made)], alice), "тот же пользователь — владелец")
    ok(not is_owner([Owner(name="alice", made=made)], again), "новый пользователь с тем же именем — нет")
    ok(is_owner([Owner(name="alice", made=None)], again), "без момента — по имени (сервис не сказал, когда создан)")
    from awgscale.agent.users.directory import record_of
    from awgscale.agent.users.models import KnownUser
    fresh = KnownUser(name="alice", ip="172.16.17.2", created_at=datetime.fromtimestamp(MADE + 0.734512, UTC))
    cached = user_of(record_of(fresh))
    stored = Owner(name="alice", made=fresh.created_at.replace(microsecond=0))
    ok(is_owner([stored], fresh) and is_owner([stored], cached) and is_owner([Owner(name="alice", made=fresh.created_at)], cached),
       "доли секунды: и свежий список, и список из кэша (в целых секундах) узнают владельца")
    ok(not is_owner([Owner(name="alice", made=made)], user_of({"name": "alice", "ip": "172.16.17.2"})), "у пользователя нет момента, а у владельца есть — не он")
    ok(owners_of("не json") == [] and owners_of('{"a": 1}') == [] and [o.name for o in owners_of('[{"name": "a"}, 5, {"x": 1}]')] == ["a"], "непрочитанное — никто")


def t_app():
    print("\n— приложение: кто я, выходы, выбор выхода")
    w = World()
    w.owners = json.dumps([{"name": "alice", "made": datetime.fromtimestamp(MADE, UTC).isoformat()}])
    w.pins = {"bob": 1}
    _invites, dispatcher = services(w)
    ask = lambda *arguments: run(dispatcher.dispatch(list(arguments), Caller.PANEL))  # noqa: E731
    alice = ask("app-me", "172.16.17.2")
    ok(alice == {"ok": True, "name": "alice", "address": "172.16.17.2", "owner": True, "expires_at": None,
                 "exit": {"pinned": None, "group": 3, "name": "exit3-de"},
                 "group": {"name": "Дом", "filter": "kids", "space": True, "members": [{"name": "alice", "address": "172.16.17.2"}, {"name": "bob", "address": "172.16.17.3"}]},
                 "filter": {"applies": "kids", "own": None, "locked": True}, "route": {"all": False, "available": True}},
       "владелец в группе-пространстве: выход группы, участники с адресами", alice)
    bob = ask("app-me", "172.16.17.3")
    ok(bob.get("owner") is False and bob.get("expires_at") == MADE + 86400 and bob.get("exit") == {"pinned": 1, "group": 3, "name": "exit1-nl"},
       "выбранный выход сильнее выхода группы; срок — секундами", bob)
    carl = ask("app-me", "172.16.17.4")
    ok(carl.get("group") == {"name": "Гости", "filter": "std", "space": False, "members": []} and carl.get("exit") == {"pinned": None, "group": None, "name": None},
       "в обычной группе других участников не видно", carl)
    nobody = ask("app-me", "172.16.17.99")
    ok(nobody == {"ok": False, "error": "это устройство не найдено в сети"}, "чужой адрес — отказ", nobody)
    exits = ask("app-exits")
    ok(exits == {"ok": True, "exits": [
        {"index": 1, "name": "exit1-nl", "up": True, "share": 50, "address": "198.51.100.38", "country": "nl", "users": 17, "online": 4},
        {"index": 3, "name": "exit3-de", "up": True, "share": 25, "address": "203.0.113.20", "country": None, "users": 2, "online": 0},
        {"index": 4, "name": "exit4-fi", "up": False, "share": 25, "address": "", "country": None, "users": 0, "online": 0}]},
       "выходы: работает ли, доля, публичный адрес (из последнего взгляда, иначе у самого туннеля, без порта), страна, сколько на нём людей и сколько из них в сети", exits)
    w.look_fails = True
    blind = ask("app-exits")
    w.look_fails = False
    ok(blind.get("ok") is True and [(e["address"], e["users"], e["online"]) for e in blind["exits"]] == [("", 0, 0), ("203.0.113.20", 0, 0), ("", 0, 0)]
       and [e["country"] for e in blind["exits"]] == ["nl", None, None],
       "взгляда на состояние нет — выходы всё равно отвечены: адрес у туннеля, счётчики нулями, страна из настроек", blind)
    ok(all(set(e) == {"index", "name", "up", "share", "address", "country", "users", "online"} for e in exits["exits"]), "у каждого выхода ровно поля договора с приложением", exits)
    chosen = ask("app-exit", "172.16.17.4", "4")
    ok(chosen.get("ok") and chosen.get("names") == ["carl"] and w.chosen == [("4", ["carl"])], "выход выбран для того, чьё устройство спросило", [chosen, w.chosen])
    ok(w.journal()[-1:] == ["carl: выход exit4-fi (в приложении)"], "в журнале — кто и куда, из приложения", w.journal())
    back = ask("app-exit", "172.16.17.4", "auto")
    ok(back.get("ok") and w.chosen[-1] == ("auto", ["carl"]) and w.journal()[-1] == "carl: выход по группе или авто (в приложении)", "auto — снова по группе или долям", w.journal())
    stranger = ask("app-exit", "172.16.17.99", "1")
    ok(stranger.get("ok") is False and len(w.chosen) == 2, "с чужого адреса ничего не выбрано", stranger)

    print("\n— своя защита")
    ok(carl.get("filter") == {"applies": "std", "own": None, "locked": False}, "по умолчанию — как у группы", carl.get("filter"))
    pro = ask("app-filter", "172.16.17.4", "proplus")
    ok(pro == {"ok": True, "name": "carl", "filter": {"applies": "proplus", "own": "proplus", "locked": False}} and w.own == {"carl": "proplus"} and w.synced[-1] == {"carl": "proplus"},
       "свой уровень: сохранён и сразу применён в блокировщике", [pro, w.own, w.synced])
    ok(w.journal()[-1] == "carl: защита — Pro++ (в приложении)", "в журнале — кто и что выбрал", w.journal())
    ok(ask("app-me", "172.16.17.4").get("filter") == {"applies": "proplus", "own": "proplus", "locked": False}, "/me показывает свой выбор")
    kid = ask("app-filter", "172.16.17.2", "off")
    ok(kid.get("ok") is False and "взрослый" in kid.get("error", "") and "alice" not in w.own, "в детской группе свою защиту не выбрать", kid)
    back = ask("app-filter", "172.16.17.4", "auto")
    ok(back.get("ok") and w.own == {} and back["filter"] == {"applies": "std", "own": None, "locked": False} and w.journal()[-1] == "carl: защита — как у группы (в приложении)",
       "auto — снова как у группы", [back, w.journal()[-1:]])
    wrong = ask("app-filter", "172.16.17.4", "max")
    ok(wrong.get("ok") is False and w.own == {}, "неизвестный уровень — отказ", wrong)

    print("\n— свой маршрут")
    synced_before = len(w.synced)
    ok(carl.get("route") == {"all": False, "available": True}, "по умолчанию — умный (по режиму сети)", carl.get("route"))
    everything = ask("app-route", "172.16.17.4", "all")
    ok(everything == {"ok": True, "name": "carl", "route": {"all": True, "available": True}} and w.whole == {"carl"} and w.compiled[-1] == ["carl"],
       "весь трафик: сохранён и сразу отдан сторожу", [everything, w.whole, w.compiled])
    ok(len(w.synced) == synced_before + 1, "и блокировщику: DNS этого человека — через выход", w.synced)
    ok(w.journal()[-1] == "carl: маршрут — весь трафик через выход (в приложении)", "в журнале — кто и что выбрал", w.journal())
    ok(ask("app-me", "172.16.17.4").get("route") == {"all": True, "available": True}, "/me показывает свой выбор")
    kid = ask("app-route", "172.16.17.2", "all")
    ok(kid.get("ok") and w.whole == {"carl", "alice"}, "маршрут в детской группе выбрать можно: это не защита", kid)
    smart = ask("app-route", "172.16.17.4", "smart")
    ok(smart.get("route") == {"all": False, "available": True} and w.whole == {"alice"} and w.compiled[-1] == ["alice"]
       and w.journal()[-1] == "carl: маршрут — умный (по режиму сети) (в приложении)", "умный — снова по режиму сети", [smart, w.whole, w.journal()[-1:]])
    wrong = ask("app-route", "172.16.17.4", "everything")
    stranger = ask("app-route", "172.16.17.99", "all")
    ok(wrong.get("ok") is False and stranger.get("ok") is False and w.whole == {"alice"}, "чужой адрес или неизвестный маршрут — отказ", [wrong, stranger])
    exits, w.exits = w.exits, []
    alone = ask("app-route", "172.16.17.4", "all")
    me = ask("app-me", "172.16.17.4")
    w.exits = exits
    ok(alone.get("ok") is False and "выход" in alone.get("error", "") and me.get("route") == {"all": False, "available": False} and w.whole == {"alice"},
       "один сервер без выходов: выбирать нечего, /me так и говорит", [alone, me.get("route")])
    w.profiles_fail = "блокировщик не отвечает (ConnectError)"
    late = ask("app-route", "172.16.17.4", "all")
    w.profiles_fail = None
    ok(late.get("ok") is False and late.get("error", "").startswith("Маршрут сохранён, но DNS не перенастроен: блокировщик") and "carl" in w.whole,
       "блокировщик не ответил — маршрут сохранён и действует, а приложение узнаёт, что DNS догонит", late)


def main():
    t_owner_code()
    t_owners()
    t_app()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
