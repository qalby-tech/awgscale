#!/usr/bin/env python3
"""Users made and removed, their groups and the notes of the last look, as the
package stores them on a real, throwaway Postgres: what is forgotten about a
removed user goes in one transaction or not at all (and is read back before it
is committed), the groups and the invitations into them are stored together,
and the notes are kept in their stored form (moments as numbers).

    PG_DSN="host=127.0.0.1 port=35443 dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=... uv run python tests/agent_people_store.py

Nothing live is touched."""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-people-store-")
import psycopg  # noqa: E402

from awgscale.agent import errors  # noqa: E402
from awgscale.agent.commands import parse  # noqa: E402
from awgscale.agent.handling import answered  # noqa: E402
from awgscale.agent.invites.store import GroupMembersInStore, InviteRecordsInStore  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.seen.models import ExitSeen  # noqa: E402
from awgscale.agent.seen.service import SeenNotes  # noqa: E402
from awgscale.agent.seen.store import StoredNotes  # noqa: E402
from awgscale.agent.spaces.service import SpacesService  # noqa: E402
from awgscale.agent.spaces.store import SpacesInStore  # noqa: E402
from awgscale.agent.store.documents import Kind  # noqa: E402
from awgscale.agent.store.store import Store  # noqa: E402
from awgscale.agent.users.accounts import AccountsService  # noqa: E402
from awgscale.agent.users.forgetting import Forgetting  # noqa: E402
from awgscale.agent.users.models import MAX_USER_GROUPS, KnownUser, Made, version_of  # noqa: E402
from awgscale.agent.users.pauses import Pauses  # noqa: E402
from awgscale.agent.users.pins import Pinning  # noqa: E402
from awgscale.agent.users.regrouping import GroupsService  # noqa: E402
from awgscale.agent.users.store import StoredHandPins, StoredPauses, StoredUserGroups  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402
from awgscale.watch.health import ExitState  # noqa: E402

DSN = os.environ["PG_DSN"]
PASSWORD_FILE = Path(TMP) / "password"
PASSWORD_FILE.write_text(os.environ["PG_PASSWORD"] + "\n")
NEW = Store(DSN, PASSWORD_FILE)
LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
USERS = [KnownUser(name="alice", ip="10.9.9.2"), KnownUser(name="bob", ip="10.9.9.3"), KnownUser(name="carol", ip="10.9.9.4")]
ASKED = []
LINES = []
PINS = Path(TMP) / "pins.map"
SETS = Path(TMP) / "spaces.sets"
LOCK = Path(TMP) / ".users.lock"
WORLD = {"spaces_fail": False, "profiles": []}


def run(work):
    return LOOP.run_until_complete(work)


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:1200])


def sql(q, *a):
    with psycopg.connect(DSN, password=os.environ["PG_PASSWORD"], autocommit=True) as c:
        cur = c.execute(q, a)
        return cur.fetchall() if cur.description else None


def held(kind):
    """What the store holds, read in a transaction of its own."""
    return run(NEW.get(kind))


def held_put(kind, value):
    run(NEW.put(kind, value))


def notes():
    return run(StoredNotes(NEW).load())


def packed(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


class Dir:
    async def list(self, force=False):
        return list(USERS)


class Watch:
    async def health(self, timeout):
        return Outcome(True)

    async def apply(self, timeout):
        return Outcome(True)

    async def pin(self, timeout):
        ASKED.append("pin")
        return Outcome(True)

    async def load(self, timeout):
        ASKED.append("spaces")
        if WORLD["spaces_fail"]:
            raise RuntimeError("сбой посередине")
        return Outcome(True)


class Profiles:
    async def sync(self, groups, users):
        WORLD["profiles"].append([(group.id, group.members) for group in groups])
        return None

    async def check(self, users):
        pass


class Exits:
    def weights(self):
        return {1: 2, 3: 1}


class Journal:
    async def write(self, text, bad=False, kind=None, who=None):
        LINES.append((text, bad, kind.value, who.value))


class Gate:
    async def add(self, name, term):
        USERS.append(KnownUser(name=name, ip="10.9.9.9"))
        return Made(ok=True, answer={"ok": True, "results": [{"name": name}]})

    async def remove(self, names):
        return None


async def host(cmd, stdin=None, timeout=30):
    if list(cmd[:2]) == ["awg", "show"]:
        return Result(0, "".join(f"{i:043d}=\t{user.ip}/32\n" for i, user in enumerate(USERS)), "")
    return Result(1, "", "")


def services():
    spaces = SpacesService(Dir(), SpacesInStore(NEW), StoredUserGroups(NEW), Watch(), host, "awg0", SETS, LOCK)
    pinning = Pinning(StoredHandPins(NEW), Watch(), PINS)
    pauses = Pauses(StoredPauses(NEW), host, "awg0", Path(TMP) / "suspended.ips")
    forgetting = Forgetting(Dir(), StoredHandPins(NEW), StoredPauses(NEW), pauses, SpacesInStore(NEW), GroupMembersInStore(NEW), pinning, Profiles(), spaces, NEW, LOCK)
    seen = SeenNotes(StoredNotes(NEW), NEW, Journal(), LOCK)
    accounts = AccountsService(NEW, Dir(), Gate(), forgetting, seen, Watch())
    groups = GroupsService(Dir(), GroupMembersInStore(NEW), InviteRecordsInStore(NEW), Exits(), pinning, SpacesInStore(NEW), spaces, Profiles(), NEW, LOCK)
    return forgetting, seen, accounts, groups


def ask(work):
    return dict(run(answered(work)))


def seed():
    held_put(Kind.USER_GROUPS, [{"id": "aaaaaa", "name": "Семья", "space": True, "exit": 3, "filter": "kids", "members": ["alice", "eve", "bob"]}])
    held_put(Kind.SPACES, [{"id": "s00001", "name": "Дом", "on": True, "members": ["eve", "carol", "bob"]}])
    held_put(Kind.SUSPENDED, {"eve": 5, "carol": 6})
    run(StoredHandPins(NEW).save({"eve": 1, "carol": 1}))


def everything():
    return [held(Kind.USER_GROUPS), held(Kind.SPACES), held(Kind.SUSPENDED), run(StoredHandPins(NEW).read())]


def main():
    sql("DROP SCHEMA public CASCADE")
    sql("CREATE SCHEMA public")

    async def make():
        async with NEW.transaction() as tx:
            await tx.create_schema()
    run(make())
    forgetting, seen, accounts, groups = services()
    refused = ask(accounts.add(parse(["add", "eve"])))
    ok(refused.get("error") == "Хранилище настроек недоступно" and "store-init" in refused.get("store", "") and [u.name for u in USERS] == ["alice", "bob", "carol"],
       "база без отметки о переносе: add отказывает до того, как пользователь создан", refused)
    sql("INSERT INTO meta (key, value) VALUES ('ready', '1')")

    print("\n— удалённый пользователь: одна транзакция")
    seed()
    before = everything()
    WORLD["spaces_fail"] = True
    try:
        run(forgetting.forget(["eve"]))
        failed = None
    except RuntimeError as e:
        failed = str(e)
    ok(failed == "сбой посередине" and everything() == before, "сбой в самом конце (сторож не загрузил пространства) — из четырёх видов не сохранено ничего", [failed, everything()])
    WORLD["spaces_fail"] = False
    ASKED.clear()
    PINS.unlink(missing_ok=True)
    run(forgetting.forget(["eve"]))
    now = everything()
    ok(now[0][0]["members"] == ["alice", "bob"] and now[1][0]["members"] == ["carol", "bob"] and now[2] == {"carol": 6} and now[3] == {"carol": 1},
       "без сбоя — ушёл из группы, пространства, пауз и ручных выходов; в базе это запись в запись", now)
    ok("kids_lists" not in now[0][0], "детская группа из записи без списков такой и осталась", now[0][0])
    ok(PINS.read_text() == "10.9.9.2 3\n10.9.9.3 3\n10.9.9.4 1\n" and "sp_gaaaaaa " in SETS.read_text() and "10.9.9.9" not in SETS.read_text() and WORLD["profiles"][-1] == [("aaaaaa", ("alice", "bob"))],
       "файлы собраны из того, что записано в этой же транзакции: закрепления без ушедшего, пространство и фильтр группы — без него", [PINS.read_text(), SETS.read_text(), WORLD["profiles"]])
    shown = ask(groups.replace(parse(["ugroups-set", packed({"groups": [{"id": "aaaaaa", "name": "Семья", "space": True, "exit": 3, "filter": "kids", "kids_lists": ["social", "nsfw"], "members": ["alice", "bob"]}],
                                                              "base": version_of(held(Kind.USER_GROUPS))})])))
    stored_now = held(Kind.USER_GROUPS)
    ok(shown.get("ok") and shown["groups"][0].get("kids_lists") == ["nsfw", "social"] and stored_now[0].get("kids_lists") == ["nsfw", "social"],
       "списки детского режима сохранены в базе и прочитаны обратно", [shown, stored_now])
    other = psycopg.connect(DSN, password=os.environ["PG_PASSWORD"])

    async def halfway():
        async with NEW.whole():
            await GroupMembersInStore(NEW).save([])
            inside = (await GroupMembersInStore(NEW).read()).document
            outside = other.execute("SELECT count(*) FROM user_groups").fetchone()[0]
            other.rollback()
            return inside, outside
    inside, outside = run(halfway())
    after = other.execute("SELECT count(*) FROM user_groups").fetchone()[0]
    other.close()
    ok(inside == () and outside == 1 and after == 0, "пока общая транзакция не закончена, внутри читается записанное, другим видно прежнее; после — новое", [inside, outside, after])

    async def two_at_once():
        """Two commands on one loop, each with a transaction of its own: one fails halfway, the other reads and writes meanwhile."""
        groups_store = GroupMembersInStore(NEW)
        pauses_store = StoredPauses(NEW)
        halfway = asyncio.Event()
        seen_by_other = []

        async def failing():
            async with NEW.whole():
                await groups_store.save([{"id": "zzzzzz", "name": "Не сохранится", "members": []}])
                halfway.set()
                await asyncio.sleep(0.3)
                raise RuntimeError("сбой посередине")

        async def other():
            await halfway.wait()
            seen_by_other.append((await groups_store.read()).document)
            async with NEW.whole():
                await pauses_store.save({})
                seen_by_other.append(await pauses_store.read())
        done = await asyncio.gather(failing(), other(), return_exceptions=True)
        return done, seen_by_other
    held_put(Kind.USER_GROUPS, [{"id": "aaaaaa", "name": "Семья", "members": ["alice"]}])
    done, seen_by_other = run(two_at_once())
    ok(isinstance(done[0], RuntimeError) and done[1] is None and seen_by_other == [({"id": "aaaaaa", "name": "Семья", "members": ["alice"]},), {}]
       and held(Kind.USER_GROUPS) == [{"id": "aaaaaa", "name": "Семья", "members": ["alice"]}] and held(Kind.SUSPENDED) == {},
       "две команды сразу на одном цикле: у каждой своя транзакция — сбой одной не задел другую, и чужого незаписанного никто не прочёл", [done, seen_by_other])

    print("\n— группы и приглашения в них")
    held_put(Kind.USER_GROUPS, [{"id": "aaaaaa", "name": "Семья", "space": False, "exit": None, "filter": "std", "members": ["alice"]},
                                 {"id": "bbbbbb", "name": "Гости", "space": False, "exit": None, "filter": "std", "members": ["bob"]}])
    invite = lambda iid, group, expires: {"id": iid, "hash": "h" + iid, "group": group, "left": 1, "total": 1, "expires_at": expires, "created": 1, "used": []}
    far = int(time.time()) + 9999
    held_put(Kind.INVITES, [invite("i00001", "aaaaaa", far), invite("i00002", "bbbbbb", far), invite("i00003", None, far), invite("i00004", "bbbbbb", 5)])
    base = version_of(held(Kind.USER_GROUPS))
    done = ask(groups.replace(parse(["ugroups-set", packed({"base": base, "groups": [{"id": "aaaaaa", "name": "Родня", "members": ["alice", "carol"], "exit": 3}]})])))
    stored = held(Kind.USER_GROUPS)
    ok(done.get("ok") and stored == [{"id": "aaaaaa", "name": "Родня", "space": False, "exit": 3, "filter": "std", "members": ["alice", "carol"]}] and done["v"] == version_of(stored)
       and set(done) == {"ok", "limit", "v", "groups"} and done["limit"] == MAX_USER_GROUPS == 30,
       "сохранённое командой лежит в базе запись в запись; ответ — с отпечатком хранимого и пределом числа групп", [done, stored])
    ok([i["id"] for i in held(Kind.INVITES)] == ["i00001", "i00003"], "приглашения в удалённую группу пропали вместе с ней (и просроченное тоже), остальные целы", held(Kind.INVITES))
    ok(PINS.read_text() == "10.9.9.2 3\n10.9.9.4 1\n", "закрепления собраны: группа — за своим выходом, ручной выбор сильнее", PINS.read_text())
    sql("ALTER TABLE invites RENAME TO invites_away")
    base = version_of(stored)
    broken = ask(groups.replace(parse(["ugroups-set", packed({"base": base, "groups": []})])))
    sql("ALTER TABLE invites_away RENAME TO invites")
    ok(broken.get("error") == "Хранилище настроек недоступно" and held(Kind.USER_GROUPS) == stored, "приглашения не записать — и группы не сохранены: вместе или никак", [broken, held(Kind.USER_GROUPS)])

    print("\n— замеченное между опросами")
    X = lambda state: [ExitSeen(index=1, name="nl", state=ExitState(state))]
    now = int(time.time())
    at = lambda seconds: datetime.fromtimestamp(seconds, UTC)
    try:
        run(seen.check(USERS, X("up"), at(now)))
        first = None
    except errors.StoreDown as e:
        first = str(e)
    ok(first is not None and "store-init" in first and LINES == [], "наблюдения ещё не перенесены в базу — сравнения нет вовсе", first)
    sql("INSERT INTO meta (key, value) VALUES ('moved:seen', '1')")
    run(seen.check(USERS[:3], X("up"), at(now)))
    first_notes = notes()
    ok(first_notes == {"v": 1, "at": now, "clients": {"alice": None, "bob": None, "carol": None}, "exits": {"1": ["up", "nl", now]}, "leaving": {}} and LINES == [],
       "первый взгляд записан в базу в хранимом виде (время — числом), журнал молчит", first_notes)
    run(seen.check(USERS[:2], X("down"), at(now + 10)))
    ok([line[0] for line in LINES] == ["Пользователь carol удалён не через панель", "Выход nl недоступен"] and LINES[1][1:] == (True, "exit", "system") and notes()["clients"] == {"alice": None, "bob": None},
       "другой взгляд: события в журнале, запомнено новое", LINES)
    run(seen.leaving(["alice"]))
    mark = notes()["leaving"]
    ok(list(mark) == ["alice"] and now + 590 <= mark["alice"] <= now + 620, "пометка «уходит» записана в базу на 10 минут", mark)
    run(seen.leaving(["bob"]))
    run(seen.back("alice"))
    ok(list(notes()["leaving"]) == ["bob"], "снята пометка одного — пометка другого на месте", notes())
    run(seen.check(USERS[1:2], X("up"), at(now + 20)))
    count = len(LINES)
    written = notes()
    run(seen.check(USERS[1:2], X("up"), at(now + 15)))
    ok(len(LINES) == count and notes() == written and written["at"] == now + 20, "взгляд, начатый раньше уже записанного, ничего не пишет", notes())
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
