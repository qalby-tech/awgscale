#!/usr/bin/env python3
"""The agent at the size it is built for: 100 000 users, every one a peer of
the tunnel. The look (the sampling job) reads `awg show awg0 dump` once and
the users once; `state` and a page of `clients` are answered from it, each well
within a bound that does not grow with the users.

    uv run python tests/test_scale.py
    SCALE_USERS=20000 uv run python tests/test_scale.py      # a smaller run

Nothing live is touched: the tunnel, the users service and the store are stand-ins.
One line per check, the last line is "N of M checks passed"."""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from awgscale.agent.adguard.models import Counters as DnsCounters  # noqa: E402
from awgscale.agent.commands import Command  # noqa: E402
from awgscale.agent.dispatcher import Dispatcher  # noqa: E402
from awgscale.agent.handling import Handled  # noqa: E402
from awgscale.agent.spaces.models import StoredSpaces  # noqa: E402
from awgscale.agent.state.ledger import Ledger  # noqa: E402
from awgscale.agent.state.models import DnsShown  # noqa: E402
from awgscale.agent.state.service import Sampling, StateService  # noqa: E402
from awgscale.agent.state.system import EntryFacts  # noqa: E402
from awgscale.agent.tops.models import Tops  # noqa: E402
from awgscale.agent.tunnel import Tunnel, peers_of  # noqa: E402
from awgscale.agent.users.models import KnownUser, StoredGroups  # noqa: E402
from awgscale.core.shell import Result  # noqa: E402
from awgscale.watch.conf import RouteMode  # noqa: E402
from awgscale.watch.health import ExitState  # noqa: E402
from awgscale.watch.models import ExitView, Status, Work  # noqa: E402

USERS = int(os.environ.get("SCALE_USERS", "100000"))
ONLINE_SHARE = 0.15
ANSWER_BOUND = 0.050                 # seconds: `state`, and a page of `clients`
PARSE_BOUND = 1.0                    # seconds: the dump of every peer, parsed
EXITS = 3
GROUPS = 30
NOW = datetime.now(UTC).replace(microsecond=0)
TMP = Path(tempfile.mkdtemp(prefix="scale-"))

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


def address(number):
    """The users' addresses: 10.64.0.2 onwards, as a /10 would give them."""
    return str(IPv4Address(int(IPv4Address("10.64.0.2")) + number))


def name(number):
    return f"user{number:06d}"


def make_dump(users, grown=0):
    """`awg show awg0 dump`: the interface's line, then one line per peer. Every 7th user is online now."""
    lines = ["PRIVATE=\tPUBLIC=\t51820\toff"]
    now = int(NOW.timestamp())
    for number in range(users):
        online = number % 7 == 0
        handshake = now - 30 if online else (0 if number % 5 == 0 else now - 86400 * (number % 30))
        lines.append(
            f"KEY{number:040d}=\tPSK\t203.0.113.{number % 250 + 1}:{40000 + number % 20000}\t{address(number)}/32\t{handshake}\t{number * 10 + grown}\t{number * 20 + grown * 3}\toff"
        )
    return "\n".join(lines) + "\n"


class FakeRun:
    """The `awg` tool: the dump it would print, counted."""

    def __init__(self, dump):
        self.dump = dump
        self.dumps = 0

    async def __call__(self, command, stdin=None, timeout=30):
        if command[-1] == "dump":
            self.dumps += 1
            return Result(0, self.dump, "")
        if command[-1] == "listen-port":
            return Result(0, "51820\n", "")
        return Result(0, "", "")


class Fakes:
    """Every other part the look asks, answering at once."""

    def __init__(self, users):
        self.users = [KnownUser(name=name(n), ip=address(n), expires_at=NOW + timedelta(hours=n % 100)) for n in range(users)]
        members = [[] for _ in range(GROUPS)]
        for n in range(users):
            if n % 3:
                members[n % GROUPS].append(name(n))
        self.groups = StoredGroups.of([{"id": f"g{g:05d}", "name": f"group {g}", "members": members[g]} for g in range(GROUPS)])
        per_exit = [[] for _ in range(EXITS)]
        for n in range(users):
            per_exit[n % EXITS].append(IPv4Address(address(n)))
        self.watch_status = Status(
            time=NOW,
            exits=[
                ExitView(index=i + 1, name=f"exit{i + 1}", iface=f"awgk{i + 1}", state=ExitState.UP, handshake=NOW, rx=1, tx=2, clients=per_exit[i], probe_ms=12.5, changed=None)
                for i in range(EXITS)
            ],
            udp_via_exit=False,
            route_mode=RouteMode.LIST,
            force_exit_nets=0,
            ru_networks=0,
            force_exit_ips=0,
            force_direct_ips=0,
            watch=Work(started=NOW, applied=NOW, last_tick=NOW, tick_seconds=0.1, ticks=1, tick_failed=0),
        )
        self.lists = 0

    # the watch service
    async def status(self, timeout):
        return self.watch_status

    def weights(self):
        return {i + 1: 1 for i in range(EXITS)}

    # the entry
    async def facts(self):
        return EntryFacts(hostname="entry", load1=0.1, cpus=2, mem_total=1, mem_used=1, uptime=1, disk_pct=1, wan_if="eth0", wan_rx=0, wan_tx=0)

    async def link_counters(self, iface):
        return (0, 0)

    async def link_there(self, iface):
        return True

    # the users
    async def list(self, force=False):
        self.lists += 1
        return self.users

    async def check_pins(self, users):
        pass

    async def check(self, users, exits=None, now=None):
        pass

    async def of(self, iface):
        return None

    async def read(self):
        return {}

    async def sync(self, users):
        return []

    async def restore(self):
        pass

    async def stats(self):
        return DnsShown(counters=DnsCounters(), tops=Tops())


class Store:
    def __init__(self, value):
        self.value = value

    async def read(self):
        return self.value


def made(users):
    dump = make_dump(users)
    run = FakeRun(dump)
    fakes = Fakes(users)
    state = StateService(
        watch=fakes,
        exits=fakes,
        tunnel=Tunnel(run, "awg0"),
        entry=fakes,
        directory=fakes,
        pins=fakes,
        seen=fakes,
        profiles=fakes,
        exit_servers=fakes,
        pause_store=Store({}),
        pauses=fakes,
        hand_pins=Store({}),
        groups=Store(fakes.groups),
        sites=Store([]),
        spaces=Store(StoredSpaces.of([])),
        dns=fakes,
        client_iface="awg0",
        domains_exit=TMP / "domains-exit.txt",
        domains_direct=TMP / "domains-direct.txt",
        lock_file=TMP / ".users.lock",
        sampling=Sampling(every=timedelta(seconds=3600), online_for=timedelta(seconds=180), keep_day_every=timedelta(hours=1)),
        ledger=Ledger(TMP / "day.json"),
    )
    dispatcher = Dispatcher()
    dispatcher.register(Command.STATE, Handled(state.show))
    dispatcher.register(Command.CLIENTS, Handled(state.clients))
    dispatcher.register(Command.DAY, Handled(state.day))
    return state, dispatcher, run, fakes


def packed(query):
    return base64.b64encode(json.dumps(query).encode()).decode()


async def timed(work):
    started = time.perf_counter()
    answer = await work
    return answer, time.perf_counter() - started


async def best_of(dispatcher, arguments, times=5):
    """The quickest of a few answers: what one answer costs, without the noise of the machine."""
    best = None
    answer = None
    for _ in range(times):
        answer, seconds = await timed(dispatcher.dispatch(arguments))
        best = seconds if best is None else min(best, seconds)
    return answer, best


async def main():
    print(f"— {USERS} users, every one a peer")
    dump = make_dump(USERS)
    started = time.perf_counter()
    peers = peers_of(dump)
    parse = time.perf_counter() - started
    ok(len(peers) == USERS, "every peer of the dump is read")
    ok(parse < PARSE_BOUND, f"the dump is parsed in {parse * 1000:.0f} ms (bound {PARSE_BOUND * 1000:.0f} ms)", parse)

    state, dispatcher, run, fakes = made(USERS)
    _, look = await timed(state.sample())
    print(f"     one look (the sampling job): {look * 1000:.0f} ms")
    ok(run.dumps == 1 and fakes.lists == 1, "one look asks the tunnel for its dump once and the users once", (run.dumps, fakes.lists))

    looking = asyncio.create_task(state.sample())
    await asyncio.sleep(0.05)                      # the look is under way: the dump read, its lines being parsed beside the loop
    answer, while_looking = await timed(dispatcher.dispatch(["state"]))
    await looking
    ok(answer.get("ok") is True and while_looking < ANSWER_BOUND * 4, f"state answers in {while_looking * 1000:.1f} ms while a look is being taken (bound {ANSWER_BOUND * 4000:.0f} ms)", while_looking)
    run.dumps = 1
    fakes.lists = 1

    answer, seconds = await best_of(dispatcher, ["state"])
    ok(answer.get("ok") is True and "clients" not in answer, "state answers without the list of users", sorted(answer)[:20])
    ok(seconds < ANSWER_BOUND, f"state answers in {seconds * 1000:.1f} ms (bound {ANSWER_BOUND * 1000:.0f} ms)", seconds)
    users = answer.get("users", {})
    ok(users.get("users") == USERS, "the users are counted", users.get("users"))
    online = sum(1 for n in range(USERS) if n % 7 == 0)
    ok(users.get("online") == online, "those online are counted", (users.get("online"), online))
    ok(len(users.get("groups", [])) == GROUPS and sum(group["users"] for group in users["groups"]) + users["ungrouped"]["users"] == USERS, "every group is counted, and those in none")
    ok(all("clients" not in exit_ for exit_ in answer["exits"]) and sum(exit_["users"] for exit_ in answer["exits"]) == USERS, "each exit says how many users it has, not who")
    ok(run.dumps == 1, "the answers did not ask the tunnel again", run.dumps)

    queries = {
        "the first page by address": {"limit": 50},
        "a page far down": {"offset": USERS - 120, "limit": 100},
        "a search by name": {"q": "user0999", "limit": 50},
        "a search by address": {"q": "10.64.1.", "limit": 50},
        "those online, by speed": {"status": "online", "sort": "speed", "limit": 100},
        "a group, by name backwards": {"group": "g00007", "sort": "name", "desc": True, "limit": 100},
        "the terms that end within a day": {"status": "expiring", "within": 86400, "sort": "expires", "limit": 100},
        "who has not been heard from for a week": {"status": "stale", "within": 7 * 86400, "limit": 15},
        "exactly some users": {"names": [name(n) for n in range(0, USERS, max(1, USERS // 60))][:60], "limit": 100},
    }
    for title, query in queries.items():
        answer, seconds = await best_of(dispatcher, ["clients", packed(query)])
        ok(answer.get("ok") is True and isinstance(answer.get("total"), int), f"clients: {title} — {answer.get('total')} in all, {len(answer.get('clients', []))} on the page", answer)
        ok(seconds < ANSWER_BOUND, f"clients: {title} in {seconds * 1000:.1f} ms (bound {ANSWER_BOUND * 1000:.0f} ms)", seconds)

    answer = await dispatcher.dispatch(["clients", packed({"q": "user000123", "limit": 5})])
    ok([client["name"] for client in answer["clients"]] == ["user000123"] and answer["total"] == 1, "a search finds exactly whom it names", answer)
    answer = await dispatcher.dispatch(["clients", packed({"status": "online", "limit": 0})])
    ok(answer["total"] == online and answer["clients"] == [], "limit 0 only counts", answer.get("total"))
    answer = await dispatcher.dispatch(["clients", packed({"limit": 101})])
    ok(answer.get("ok") is False, "a page larger than 100 is refused", answer)
    answer = await dispatcher.dispatch(["clients", packed({"sort": "nonsense"})])
    ok(answer.get("ok") is False, "an order that does not exist is refused", answer)
    first = await dispatcher.dispatch(["clients", packed({"limit": 100})])
    second = await dispatcher.dispatch(["clients", packed({"offset": 100, "limit": 100})])
    ok(first["clients"][-1]["ip"] == address(99) and second["clients"][0]["ip"] == address(100), "pages follow one another without a gap")

    # the day: counted from the first question, folded in at every look
    since = str(int(NOW.timestamp()) - 3600)
    answer = await dispatcher.dispatch(["day", since])
    ok(answer.get("ok") is True and answer["down"] == 0, "a day first asked about starts at nought", answer)
    run.dump = make_dump(USERS, grown=1000)
    state.changed()
    _, look = await timed(state.sample())
    print(f"     the next look: {look * 1000:.0f} ms")
    answer, seconds = await timed(dispatcher.dispatch(["day", since]))
    ok(answer["down"] == USERS * 3000 and answer["up"] == USERS * 1000, "the growth of every user is counted into the day", (answer["down"], answer["up"]))
    ok(len(answer["top"]) == 5 and answer["top"][0]["name"] == name(0), "the day names the five who took the most, not everybody", answer["top"])
    ok(seconds < 0.25, f"the day is answered in {seconds * 1000:.0f} ms", seconds)
    page = await dispatcher.dispatch(["clients", packed({"status": "online", "sort": "speed", "limit": 1})])
    ok(page["clients"][0]["down_bps"] > 0, "speeds come from two looks", page["clients"][0])
    await scoped_rules()
    print(f"{PASS} of {PASS + FAIL} checks passed")
    return FAIL == 0


async def scoped_rules():
    """Rules for some groups that go abroad or directly, at the size they are made for: hundreds of users, about
    twenty rules, thousands of names, a few groups."""
    from awgscale.agent.adguard import group_lists, profiles
    from awgscale.agent.rules.scoped import routed_groups, scoped_names, scoped_rules_text, view_groups
    from awgscale.agent.sites.groups import direct_entries, flat_entries
    from awgscale.agent.sites.routing import ROUTE_IDS_RESERVE, UPSTREAM_BYTES, ipset_text, route_client_fits, upstreams
    from awgscale.agent.sites.scoped import GroupDns
    from awgscale.agent.users.models import UserGroup

    people = 300
    names = 2500
    routed = 5
    print(f"\n— rules for some groups: {people} users, 20 rules, {names} names, {routed} groups sent abroad or directly")
    domains = [f"site{n:05d}.example{n % 7}.com" for n in range(names)]
    rules = []
    for index in range(20):
        chunk = domains[index * names // 20:(index + 1) * names // 20]
        rule = {"id": f"r{index:05d}", "name": f"rule {index}", "on": True, "route": "direct" if index % 4 == 3 else "exit", "items": chunk}
        if index >= 15:
            rule["groups"] = [f"g{index - 15:05d}"]
            rule["items"] = chunk[:40] + ["example%d.com" % (index % 7)]       # a domain above many names for everybody
        rules.append(rule)
    groups = [UserGroup(id=f"g{n:05d}", name=f"group {n}", filter=("pro" if n % 2 else "std"), members=tuple(f"u{m:04d}" for m in range(n * 50, n * 50 + 50))) for n in range(6)]
    users = [KnownUser(name=f"u{m:04d}", ip=str(IPv4Address("10.64.0.2") + m)) for m in range(people)]

    started = time.perf_counter()
    pairs = scoped_names(rules)
    global_groups = view_groups(rules, None)
    text = ipset_text("".join(e + "\n" for e in flat_entries(global_groups)), "".join(e + "\n" for e in direct_entries(global_groups)), pairs)
    chain = scoped_rules_text(rules)
    compiled = time.perf_counter() - started
    plain = ipset_text("".join(e + "\n" for e in flat_entries(global_groups)), "".join(e + "\n" for e in direct_entries(global_groups)))
    print(f"     compiled in {compiled * 1000:.0f} ms: {len(pairs)} names of rules for groups, domains.ipset {len(text)} bytes ({len(plain)} without them), {len(text.splitlines())} lines")
    ok(compiled < 2.0 and chain.count("\n") == 5, f"the names of the rules for groups and the resolver's file are made in {compiled * 1000:.0f} ms (bound 2000 ms)", compiled)

    class Rules:
        async def read(self):
            return rules

    class Routing:
        def mode(self):
            from awgscale.agent.commands import RouteMode as Mode
            return Mode.LIST

        def direct_names(self):
            return []

    started = time.perf_counter()
    routes = await GroupDns(Rules(), Routing()).of()
    made_dns = time.perf_counter() - started
    biggest = max(len(json.dumps(list(route.upstreams))) for route in routes.values())
    ok(sorted(routes) == routed_groups(rules) and made_dns < 2.0, f"the DNS of {len(routes)} groups made in {made_dns * 1000:.0f} ms", made_dns)
    print(f"     the largest client's upstreams: {biggest} bytes of {UPSTREAM_BYTES - ROUTE_IDS_RESERVE} it may take (the blocker takes {UPSTREAM_BYTES} with the addresses)")
    ok(all(route_client_fits(route.upstreams) for route in routes.values()) == (biggest <= UPSTREAM_BYTES - ROUTE_IDS_RESERVE), "the check of the size agrees with the size")
    want = profiles.wanted(groups, users, {}, {users[0].name, users[60].name}, routes)
    before = profiles.wanted(groups, users, {}, {users[0].name, users[60].name})
    largest = max(len(json.dumps(profile.wire())) for profile in want)
    print(f"     clients of the blocker: {len(want)} (without the rules for groups: {len(before)}), the largest request {largest} bytes")
    ok(largest <= UPSTREAM_BYTES, f"every client of the blocker fits one request ({largest} of {UPSTREAM_BYTES} bytes)", largest)
    needs = group_lists.needs_of(groups, {}, routed_groups(rules))
    needs_before = group_lists.needs_of(groups, {})
    widest = max(bin(group_lists.client_bits(needs, source)).count("1") for source in group_lists.sources_of(needs))
    widest_before = max(bin(group_lists.client_bits(needs_before, source)).count("1") for source in group_lists.sources_of(needs_before))
    print(f"     the groups' own lists name {len(needs)} clients (without: {len(needs_before)}); a line of a list names up to {widest} (without: {widest_before})")
    ok(widest <= 4 * max(widest_before, 1), "the file of the groups' lists grows by the variants only (a line names a few more clients)", (widest, widest_before))


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
