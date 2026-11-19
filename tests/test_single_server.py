#!/usr/bin/env python3
"""A single server (SINGLE_SERVER=1 in cascade.conf): a server without exits, whose
users' internet leaves from the server itself.

What is checked: the seed of a new server is one; the agent and the watch service
read it and refuse both mistakes (exits named on a single server, no exit on a
cascade); the file the agent compiles says it and reads back the same; the rules
mark nothing; the cascade's answers do not change by a single field.

    uv run python tests/test_single_server.py
One line per check, the last line is "N of M checks passed".
"""
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from awgscale.agent.cascade.conf import parse, text_of  # noqa: E402
from awgscale.agent.cascade.models import checked  # noqa: E402
from awgscale.watch.cascade import CascadeStatus  # noqa: E402
from awgscale.watch.conf import CascadeConf, RouteMode  # noqa: E402
from awgscale.service.errors import Refused  # noqa: E402
from awgscale.watch.models import Status, Work  # noqa: E402
from awgscale.watch.places import Places  # noqa: E402
from awgscale.watch.rules import mangle_rules, nat_rules  # noqa: E402

SEED = Path(ROOT, "cascade", "cascade.conf").read_text()
CASCADE = Path(ROOT, "tests", "cascade.conf").read_text()
AN_EXIT = '"1:awgk1:10.77.1.2:exit1:1"'

passed = 0
failed = 0


def ok(condition: bool, what: str, shown: object = None) -> None:
    global passed, failed
    if condition:
        passed += 1
        print("ok   " + what)
    else:
        failed += 1
        print("FAIL " + what + ("" if shown is None else f" → {shown!r}"[:400]))


def watch_load(text: str) -> CascadeConf | str:
    """The watch service's reading of a file: the settings, or the refusal's text."""
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "cascade.conf").write_text(text)
        try:
            return CascadeConf.load(Places(dir=Path(directory), run=Path(directory, "run")))
        except Refused as e:
            return str(e)


def agent_check(document: dict) -> str:
    """The agent's verdict on a document: "" when it is taken, the refusal's text when not."""
    try:
        checked(document)
    except ValueError as e:
        return str(e)
    return ""


print("\n— the seed of a new server")
seed = parse(SEED)
ok(seed.single_server and seed.exits == (), "the seed is a single server with no exits", seed)
ok(checked(seed.wire()) == seed, "the agent takes the seed as it is")
conf = watch_load(SEED)
ok(isinstance(conf, CascadeConf) and conf.single_server and conf.exits == (), "the watch service reads it as a single server", conf)

print("\n— the mistakes are refused, by both")
with_exit = SEED.replace("EXITS=(\n", "EXITS=(\n    " + AN_EXIT + "\n")
ok(isinstance(watch_load(with_exit), str), "watch: an exit named on a single server is refused", watch_load(with_exit))
without_flag = SEED.replace("SINGLE_SERVER=1\n", "")
ok(isinstance(watch_load(without_flag), str), "watch: no exit and no SINGLE_SERVER is refused as before", watch_load(without_flag))
document = seed.wire()
ok("single_server" in agent_check(dict(document, exits=[{"index": 1, "iface": "awgk1", "tip": "10.77.1.2", "name": "exit1", "weight": 1}])),
   "agent: exits in a single server's document are refused")
ok("выход" in agent_check(dict(document, single_server=False)), "agent: a cascade without an exit is refused as before")
ok("single_server" in agent_check(dict(document, single_server="yes")), "agent: single_server is true or false, nothing else")

print("\n— the first exit")
grown = checked(dict(document, single_server=False, exits=[{"index": 1, "iface": "awgk1", "tip": "10.77.1.2", "name": "exit1", "weight": 99}]))
ok(not grown.single_server and grown.probe_targets and grown.dns_via_exit and grown.udp_via_exit,
   "the seed with its first exit is a cascade with probe targets, DNS and calls through the exit", grown)
ok("SINGLE_SERVER" not in text_of(grown) and "PROBE_TARGETS=(1.1.1.1 9.9.9.9)" in text_of(grown), "…and its file says so")

print("\n— the compiled file")
text = text_of(checked(document))
ok("SINGLE_SERVER=1\n" in text and parse(text) == checked(document), "a single server's file says so and reads back the same", text[-200:])
cascade = checked(parse(CASCADE).wire())
ok("SINGLE_SERVER" not in text_of(cascade) and "single_server" not in cascade.wire(), "a cascade's file and document have no trace of it")

print("\n— the rules")
rules = mangle_rules(conf) if isinstance(conf, CascadeConf) else ""
ok("-A CASCADE -d 172.16.17.0/24 -j RETURN" in rules and "MARK" not in rules.replace(":CASCADE-MARK", "").replace("-F CASCADE-MARK", ""),
   "nothing is marked: the internet leaves from this server", rules)
ok("MASQUERADE" not in (nat_rules(conf) if isinstance(conf, CascadeConf) else "MASQUERADE"), "no NAT towards an exit")

print("\n— the answers of a cascade stay as they were")
now = datetime.now(UTC)
work = Work(started=now, applied=now, last_tick=now, tick_seconds=0.1, ticks=1, tick_failed=0)
plain = CascadeStatus(time=now, exits=(), udp_via_exit=False, route_mode=RouteMode.LIST, force_exit_nets=0, ru_networks=0, force_exit_ips=0, force_direct_ips=0)
single = CascadeStatus(time=now, exits=(), udp_via_exit=False, route_mode=RouteMode.LIST, force_exit_nets=0, ru_networks=0, force_exit_ips=0, force_direct_ips=0, single_server=True)
ok("single_server" not in Status.of(plain, work).model_dump(mode="json"), "watch status of a cascade: no single_server field")
ok(Status.of(single, work).model_dump(mode="json").get("single_server") is True, "watch status of a single server: single_server true")

print(f"\n{passed} of {passed + failed} checks passed")
sys.exit(1 if failed else 0)
