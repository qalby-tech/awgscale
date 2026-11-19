#!/usr/bin/env python3
"""A user made, removed or paused among 100 000 others touches that user only:
one `awg set awg0 peer <key> …` (or `… remove`), one block appended to or cut
out of awg0.conf with every other byte as it was, one address added to or
taken from the pause set — and never `awg setconf` or `syncconf`.

The users service (awgscale.users) runs with a stand-in for its database and
for the `awg` tool; the tunnel's file is a real file of 100 000 peers.

    uv run python tests/test_peers.py
    SCALE_USERS=20000 uv run python tests/test_peers.py

One line per check, the last line is "N of M checks passed"."""
import asyncio
import base64
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from ipaddress import IPv4Address
from pathlib import Path

from pydantic import SecretStr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from awgscale.agent.users.models import KnownUser  # noqa: E402
from awgscale.agent.users.pauses import SET_NAME, Pauses  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402
from awgscale.users.conf import TunnelFile, parse, render  # noqa: E402
from awgscale.users.kernel import Kernel  # noqa: E402
from awgscale.users.models import AddRequest, Status, User  # noqa: E402
from awgscale.users.service import UsersService  # noqa: E402
from awgscale.users.settings import UsersSettings  # noqa: E402

USERS = int(os.environ.get("SCALE_USERS", "100000"))
TMP = Path(tempfile.mkdtemp(prefix="peers-"))
NOW = datetime.now(UTC).replace(microsecond=0)
INTERFACE = "[Interface]\nPrivateKey = c2VydmVyLXByaXZhdGUta2V5LWZvci10aGUtdGVzdHMhIQ==\nAddress = 10.64.0.1/14\nListenPort = 51820\nJc = 4\n"
WHOLE_INTERFACE = ("setconf", "syncconf", "addconf")

PASS = 0
FAIL = 0


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:500])


def key(number, kind=b"K"):
    return base64.b64encode(kind + number.to_bytes(31, "big")).decode()


def user(number):
    return User(
        name=f"user{number:06d}",
        ip=IPv4Address(int(IPv4Address("10.64.0.2")) + number),
        public_key=key(number),
        private_key=SecretStr(key(number, b"P")),
        preshared_key=SecretStr(key(number, b"S")),
        expires_at=None,
        created_at=NOW,
    )


class Store:
    """The database: the users in memory, asked only the way the service asks it."""

    def __init__(self, users):
        self.users = {each.name: each for each in users}
        self.full_reads = 0

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def all(self):
        self.full_reads += 1
        return list(self.users.values())

    async def get(self, name):
        return self.users.get(name)

    async def count(self):
        return len(self.users)

    async def addresses(self):
        return {each.ip for each in self.users.values()}

    async def known(self, names):
        return {name for name in names if name in self.users}

    async def insert(self, made):
        self.users[made.name] = made

    async def delete(self, name):
        self.users.pop(name, None)

    async def delete_exactly(self, made):
        self.users.pop(made.name, None)


class Awg:
    """The `awg` tool: every command it is given, in order."""

    def __init__(self):
        self.commands = []

    async def __call__(self, command, stdin=None, timeout=30):
        self.commands.append(list(command))
        return Result(0, "", "")


class Scheduler:
    def add_job(self, *args, **kwargs):
        pass

    def get_job(self, job_id):
        return None

    def remove_job(self, job_id):
        pass


def whole_interface(commands):
    return [command for command in commands if any(word in command for word in WHOLE_INTERFACE)]


async def main():
    print(f"— {USERS} users, every one a peer of awg0 and a block of awg0.conf")
    users = [user(number) for number in range(USERS)]
    conf = TMP / "awg0.conf"
    started = time.perf_counter()
    conf.write_text(render(parse(INTERFACE)[0], users) + "\n")
    print(f"     the file: {conf.stat().st_size // 1024} KiB, written in {(time.perf_counter() - started) * 1000:.0f} ms")
    before = conf.read_bytes()
    settings = UsersSettings(iface="awg0", conf=conf, awg=Path("awg"), **{"USERS_MAX": USERS + 10})
    store = Store(users)
    awg = Awg()
    service = UsersService(settings, store, Kernel(settings, awg), TunnelFile(conf), None, Scheduler())

    started = time.perf_counter()
    made = await service.add(AddRequest(name="newcomer"))
    took = time.perf_counter() - started
    peer_commands = [command for command in awg.commands if command[1:4] == ["set", "awg0", "peer"]]
    newcomer = store.users["newcomer"]
    ok(len(awg.commands) == 1 and len(peer_commands) == 1 and peer_commands[0][4] == newcomer.public_key and peer_commands[0][-2:] == ["allowed-ips", f"{newcomer.ip}/32"],
       "a new user: exactly one command, `awg set awg0 peer <key> … allowed-ips <address>/32`", awg.commands)
    ok(str(made.ip) == str(IPv4Address(int(IPv4Address("10.64.0.2")) + USERS)), "the address is the lowest free one of the tunnel's /14", made.ip)
    after = conf.read_bytes()
    block = after[len(before):].decode()
    ok(after.startswith(before) and block.count("[Peer]") == 1 and f"PublicKey = {newcomer.public_key}\n" in block, "awg0.conf: one block appended, every byte before it as it was")
    ok(store.full_reads == 0, "the database is asked about one name, a count and the addresses — never every user", store.full_reads)
    ok(took < 1.0, f"made in {took * 1000:.0f} ms beside {USERS} others")

    awg.commands.clear()
    started = time.perf_counter()
    removed = await service.remove(["user000500"])
    took = time.perf_counter() - started
    gone = users[500]
    ok(removed[0].status is Status.REMOVED and awg.commands == [["awg", "set", "awg0", "peer", gone.public_key, "remove"]], "a user removed: exactly `awg set awg0 peer <key> remove`", awg.commands)
    text = conf.read_text()
    expected = render(parse(INTERFACE)[0], [*users[:500], *users[501:], newcomer]) + "\n"
    ok(text == expected, "awg0.conf: that one block cut out, the others byte for byte as they were")
    ok(store.full_reads == 0, "…and the database again asked about that one name only", store.full_reads)
    ok(took < 1.0, f"removed in {took * 1000:.0f} ms beside {USERS} others")

    awg.commands.clear()
    await service.remove(["newcomer"])
    ok(conf.read_text() == render(parse(INTERFACE)[0], [*users[:500], *users[501:]]) + "\n", "the last block cut out: the blank line at the end stays")
    ok(not whole_interface(awg.commands), "neither setconf nor syncconf on the whole interface", awg.commands)

    print("\n— a pause among them: one address into the set, one out")
    host = Awg()
    listed = {"out": "create cascade_susp hash:ip\nadd cascade_susp 10.64.0.9\n"}

    async def kernel(command, stdin=None, timeout=30):
        host.commands.append(list(command))
        if command[:2] == ["ipset", "list"]:
            return Result(0, listed["out"], "")
        return Result(0, "", "")

    paused = {users[7].name: NOW}

    class PauseStore:
        async def read(self):
            return dict(paused)

        async def save(self, pauses):
            pass

    known = [KnownUser(name=each.name, ip=str(each.ip)) for each in users]
    pauses = Pauses(PauseStore(), kernel, "awg0", TMP / "suspended.ips")
    paused[users[42].name] = NOW
    await pauses.sync(known)
    changes = [command for command in host.commands if command[:2] == ["ipset", "add"] or command[:2] == ["ipset", "del"] or command[:2] == ["ipset", "restore"] or "flush" in command]
    ok(changes == [["ipset", "add", SET_NAME, str(users[42].ip), "-exist"]], "one more paused: `ipset add` of that address, nothing reloaded", changes)
    host.commands.clear()
    listed["out"] = f"create cascade_susp hash:ip\nadd cascade_susp 10.64.0.9\nadd cascade_susp {users[42].ip}\n"
    del paused[users[7].name]
    await pauses.sync(known)
    changes = [command for command in host.commands if command[:2] in (["ipset", "add"], ["ipset", "del"], ["ipset", "restore"]) or "flush" in command]
    ok(changes == [["ipset", "del", SET_NAME, str(users[7].ip), "-exist"]], "one let through again: `ipset del` of that address", changes)

    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
