#!/usr/bin/env python3
"""Spaces and invitations of the package on a real, throwaway Postgres: what
the commands store is read back record for record in the stored form (moments as
numbers, a fingerprint instead of the link), and what is in the store is what
the commands work on.

    PG_DSN="host=127.0.0.1 port=35443 dbname=vpn user=vpn connect_timeout=3" PG_PASSWORD=... uv run python tests/agent_invites_store.py

Nothing live is touched."""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="agent-invites-store-")
import psycopg  # noqa: E402

from awgscale.agent.commands import parse  # noqa: E402
from awgscale.agent.handling import answered  # noqa: E402
from awgscale.agent.invites.models import hash_of  # noqa: E402
from awgscale.agent.invites.service import InvitesService  # noqa: E402
from awgscale.agent.invites.store import GroupMembersInStore, InvitesInStore  # noqa: E402
from awgscale.agent.ports import Outcome  # noqa: E402
from awgscale.agent.protocol import Caller  # noqa: E402
from awgscale.agent.spaces.service import SpacesService  # noqa: E402
from awgscale.agent.spaces.store import SpacesInStore  # noqa: E402
from awgscale.agent.store.documents import Kind  # noqa: E402
from awgscale.agent.store.store import Store  # noqa: E402
from awgscale.agent.users.models import KnownUser, version_of  # noqa: E402
from awgscale.agent.users.store import StoredUserGroups  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402

DSN = os.environ["PG_DSN"]
PASSWORD_FILE = Path(TMP) / "password"
PASSWORD_FILE.write_text(os.environ["PG_PASSWORD"] + "\n")
NEW = Store(DSN, PASSWORD_FILE)
LOOP = asyncio.new_event_loop()
PASS = FAIL = 0
USERS = [KnownUser(name="alice", ip="10.9.9.2"), KnownUser(name="bob", ip="10.9.9.3")]
LINES = []


def run(work):
    return LOOP.run_until_complete(work)


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:900])


def sql(q, *a):
    with psycopg.connect(DSN, password=os.environ["PG_PASSWORD"], autocommit=True) as c:
        cur = c.execute(q, a)
        return cur.fetchall() if cur.description else None


def held(kind):
    """What the store holds, read in a transaction of its own."""
    return run(NEW.get(kind))


def held_put(kind, value):
    run(NEW.put(kind, value))


def packed(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()


class Dir:
    async def list(self, force=False):
        return list(USERS)


class Users:
    async def set_expiry(self, names, term):
        return Outcome(True)

    async def config(self, name):
        return {"ok": True, "name": name, "conf": "[Interface]\n", "uri": "vpn://x", "conf_png": "AA", "uri_png": "BB"}


class Newcomers:
    async def ready(self):
        await NEW.ping()

    async def forget(self, name):
        pass

    async def add(self, name):
        USERS.append(KnownUser(name=name, ip=f"10.9.9.{len(USERS) + 2}"))
        return True

    async def pin(self, groups, users):
        LINES.append("pin")

    async def filter(self, groups, users):
        LINES.append("filter")


class Watch:
    async def health(self, timeout):
        return Outcome(True)

    async def apply(self, timeout):
        return Outcome(True)

    async def pin(self, timeout):
        return Outcome(True)

    async def load(self, timeout):
        return Outcome(True)


class Notes:
    async def made(self, text, caller):
        LINES.append(text)

    async def used(self, text):
        LINES.append(text)


async def host(cmd, stdin=None, timeout=30):
    return Result(0, "".join(f"{i:043d}=\t{user.ip}/32\n" for i, user in enumerate(USERS)), "")


def services():
    sets = Path(TMP) / "spaces.sets"
    lock = Path(TMP) / ".users.lock"
    spaces = SpacesService(Dir(), SpacesInStore(NEW), StoredUserGroups(NEW), Watch(), host, "awg0", sets, lock)
    invites = InvitesService(InvitesInStore(NEW), GroupMembersInStore(NEW), Dir(), Users(), Newcomers(), Watch(), SpacesInStore(NEW), spaces, Notes(), lock, Path(TMP) / ".invite.lock")
    return spaces, invites


def ask(work):
    return dict(run(answered(work)))


def main():
    sql("DROP SCHEMA public CASCADE")
    sql("CREATE SCHEMA public")

    async def make():
        async with NEW.transaction() as tx:
            await tx.create_schema()
    run(make())
    sql("INSERT INTO meta (key, value) VALUES ('ready', '1')")
    spaces, invites = services()
    print("\n— пространства")
    held_put(Kind.SPACES, [{"id": "s00001", "name": "Дом", "on": True, "members": ["alice", "bob", "gone"]}])
    held_put(Kind.USER_GROUPS, [{"id": "aaaaaa", "name": "Семья", "space": True, "exit": 3, "filter": "kids", "members": ["alice"]}])
    shown = ask(spaces.show(parse(["spaces-get"])))
    ok(shown["ok"] and shown["v"] == version_of(held(Kind.SPACES)) and shown["spaces"][0]["members"] == ["alice", "bob"], "записанное в базе команда показывает с отпечатком хранимого списка (без неизвестных пользователей)", shown)
    change = [{"id": "s00001", "name": "Дом 2", "on": False, "members": ["bob"]}, {"name": "Новое", "on": True, "members": ["alice", "bob"]}]
    done = ask(spaces.replace(parse(["spaces-set", packed({"base": shown["v"], "spaces": change})])))
    stored = held(Kind.SPACES)
    ok(done["ok"] and [s["name"] for s in stored] == ["Дом 2", "Новое"] and stored[0] == {"id": "s00001", "name": "Дом 2", "on": False, "members": ["bob"]} and done["v"] == version_of(stored),
       "сохранённое командой читается из базы запись в запись, отпечаток — хранимого", [done, stored])
    stale = ask(spaces.replace(parse(["spaces-set", packed({"base": shown["v"], "spaces": []})])))
    ok(stale.get("conflict") is True and len(held(Kind.SPACES)) == 2, "изменение поверх старого отпечатка отклонено, в базе ничего не тронуто", stale)
    print("\n— приглашения")
    made = ask(invites.add(parse(["invite-add", packed({"group": "aaaaaa", "uses": 2, "days": 3})]), Caller.PANEL))
    stored = held(Kind.INVITES)
    ok(made["ok"] and len(stored) == 1 and stored[0]["hash"] == hash_of(made["token"]) and made["token"] not in json.dumps(stored) and stored[0]["left"] == 2 and stored[0]["group"] == "aaaaaa"
       and abs(stored[0]["expires_at"] - time.time() - 3 * 86400) < 5 and abs(stored[0]["created"] - time.time()) < 5 and ask(invites.list(parse(["invite-list"])))["invites"] == made["invites"],
       "созданное хранится так: отпечаток вместо ссылки, остаток, срок числом; список тот же, что в ответе", [made, stored])
    info = ask(invites.info(parse(["invite-info", made["token"]])))
    ok(info == {"ok": True, "group": "Семья", "left": 2, "total": 2, "expires_at": stored[0]["expires_at"]}, "страница приглашения видит группу и остаток", info)
    got = ask(invites.redeem(parse(["invite-redeem", made["token"], "eve"])))
    ok(got.get("uri") == "vpn://x" and got["left"] == 1 and got["group"] == "Семья" and held(Kind.INVITES)[0]["left"] == 1 and held(Kind.INVITES)[0]["used"] == ["eve"]
       and held(Kind.USER_GROUPS)[0]["members"] == ["alice", "eve"] and "pin" in LINES and "filter" in LINES and "\nsp_gaaaaaa " in (Path(TMP) / "spaces.sets").read_text(),
       "гость создан: остаток и имя записаны, он в группе, закрепление, фильтр и пространство группы пересобраны", [got, held(Kind.INVITES), held(Kind.USER_GROUPS), LINES])
    held_put(Kind.INVITES, held(Kind.INVITES) + [{"id": "zzzzzz", "hash": hash_of("x" * 22), "group": None, "left": 1, "total": 1, "expires_at": int(time.time()) - 5, "created": 1, "used": []}])
    listed = ask(invites.list(parse(["invite-list"])))
    ok([i["id"] for i in listed["invites"]] == [made["id"]] and set(listed["invites"][0]) == set(made["invites"][0]) and listed["invites"][0]["left"] == 1,
       "просроченное, лежащее в базе, не показано; остальное — в том же виде", listed)
    gone = ask(invites.remove(parse(["invite-remove", made["id"]]), Caller.PANEL))
    ok(gone["ok"] and gone["invites"] == [] and held(Kind.INVITES) == [], "отозванное исчезает из базы (и просроченное вместе с ним, как прежде)", [gone, held(Kind.INVITES)])
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
