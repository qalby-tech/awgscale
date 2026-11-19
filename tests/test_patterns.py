#!/usr/bin/env python3
"""The regular expressions that were rewritten to be readable (named groups,
re.VERBOSE, an example in a comment) match exactly what they matched before.

Each pattern is asked about sample lines three ways (match, fullmatch,
search) next to the one-line text it had before; the whole match and every
named group must be the same. The samples also say what is expected, so this
file is the list of examples too.

    uv run python tests/test_patterns.py
One line per check, the last line is "N of M checks passed".
"""
import importlib
import os
import re
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from awgscale.agent import commands, labels  # noqa: E402
from awgscale.agent.adguard import api as adguard_api, group_lists  # noqa: E402
from awgscale.agent.cascade import models as cascade_models  # noqa: E402
from awgscale.agent.sites import entries, lists, probe, routing  # noqa: E402
from awgscale.agent.spaces import sets  # noqa: E402
from awgscale.agent.users import pauses  # noqa: E402
from awgscale.bot import commands as bot_commands  # noqa: E402
from awgscale.core import patterns  # noqa: E402
from awgscale.exits import models as exits_models  # noqa: E402
from awgscale.panel import models as panel_models, web  # noqa: E402
from awgscale.panel.routes import invites  # noqa: E402
from awgscale.tunnel import firewall, keeper  # noqa: E402
from awgscale.watch import lists as watch_lists, spaces, tools  # noqa: E402

PASS = FAIL = 0


def ok(cond, text, seen=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("ok  ", text)
    else:
        FAIL += 1
        print("FAIL", text, "" if seen is None else "→ " + repr(seen)[:500])


def seen_by(pattern, line, way):
    found = getattr(pattern, way)(line)
    return None if found is None else (found.group(0), found.span())


def same_as_before(name, new, old_text, old_flags, samples):
    """samples: (line, expected: None for no match by `match`, or {group: value} for a match)."""
    old = re.compile(old_text, old_flags)
    for line, expected in samples:
        ways = {way: (seen_by(old, line, way), seen_by(new, line, way)) for way in ("match", "fullmatch", "search")}
        ok(all(before == after for before, after in ways.values()), f"{name}: {line!r} — найдено то же, что прежним выражением", ways)
        found = new.match(line)
        if expected is None:
            ok(found is None, f"{name}: {line!r} — не подходит", found)
        else:
            groups = {group: found.group(group) for group in expected} if found else None
            ok(found is not None and groups == expected, f"{name}: {line!r} — подходит{', ' + str(expected) if expected else ''}", groups)


def t_watch():
    print("\n— сторож (watch): сети, адреса, наборы")
    same_as_before("NET_RE", watch_lists.NET_RE, r"^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$", 0,
                   [("149.154.160.0/20", {}), ("91.108.4.1", None), ("149.154.160.0/20\n", {}), ("1.2.3/20", None), ("1.2.3.4/200", None), (" 1.2.3.4/8", None)])
    same_as_before("NET_OR_IP_RE", watch_lists.NET_OR_IP_RE, r"^[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?$", 0,
                   [("149.154.160.0/20", {}), ("91.108.4.1", {}), ("91.108.4.1/", None), ("1234.1.1.1", None), ("1.1.1.1 x", None)])
    same_as_before("IP_RE", tools.IP_RE, r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$", 0,
                   [("172.16.17.23", {}), ("172.16.17.23 timeout 0", None), ("172.16.17", None), ("1.2.3.4\n", {})])
    same_as_before("ENTRIES_RE", tools.ENTRIES_RE, r"^Number of entries: (?P<count>\d+)", re.MULTILINE,
                   [("Number of entries: 8664", {"count": "8664"}), ("Name: x\nNumber of entries: 3\n", None), ("Number of  entries: 3", None), ("Numberofentries:3", None)])
    ok(tools.ENTRIES_RE.search("Name: x\nNumber of entries: 3\n")["count"] == "3", "ENTRIES_RE: число записей — в любой строке заголовка")
    same_as_before("SPACE_NAME_RE", spaces.SPACE_NAME_RE, r"^sp_[a-z0-9]{1,16}$", 0,
                   [("sp_ab12", {}), ("sp_", None), ("SP_ab12", None), ("sp_" + "a" * 17, None), ("sp_ab12 hash:ip", None)])


def t_core():
    print("\n— общие имена (core.patterns)")
    same_as_before("EXIT_NAME_RE", patterns.EXIT_NAME_RE, r"^[A-Za-z0-9._-]{1,32}\Z", 0,
                   [("exit1-nl", {}), ("nl.2", {}), ("", None), ("a" * 33, None), ("exit 1", None), ("exit1\n", None)])
    key = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
    same_as_before("KEY_RE", patterns.KEY_RE, r"^[A-Za-z0-9+/]{43}=\Z", 0,
                   [(key, {}), (key[:-1], None), (key + "\n", None), ("=" + key[1:], None), (key[:-2] + "==", None)])
    same_as_before("HOST_RE", patterns.HOST_RE, r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?\Z", 0,
                   [("example.com", {}), ("cdn.example.co.uk", {}), ("a", {}), ("example.com.", None), ("-x.com", None), ("Example.com", None), ("a" * 253, {}), ("a" * 254, None)])
    same_as_before("URL_PATH_RE", patterns.URL_PATH_RE, r"""^/[A-Za-z0-9._~%!$&'()*+,;=:@/?-]{0,600}\Z""", 0,
                   [("/watch?v=abc", {}), ("/", {}), ("/a b", None), ("/a#b", None), ("status", None), ("/" + "a" * 600, {}), ("/" + "a" * 601, None), ("/ok\n", None)])


def t_agent():
    print("\n— агент: команды, группы, блокировка, сайты")
    same_as_before("TERM_RE", commands.TERM_RE, r"^[1-9][0-9]{0,2}[hdw]\Z", 0,
                   [("12h", {}), ("7d", {}), ("999w", {}), ("0d", None), ("1000d", None), ("7m", None), ("7d\n", None)])
    same_as_before("BASE64_RE", commands.BASE64_RE, r"^[A-Za-z0-9+/]+={0,2}\Z", 0,
                   [("eyJhIjoxfQ==", {}), ("QQ==", {}), ("QQ===", None), ("==", None), ("a b", None), ("eyJ9\n", None)])
    same_as_before("EXIT_INDEX_RE", commands.EXIT_INDEX_RE, r"^[1-9][0-9]?\Z", 0,
                   [("1", {}), ("12", {}), ("0", None), ("100", None), ("01", None)])
    same_as_before("labels.ID_RE", labels.ID_RE, r"^[a-z0-9]{6}\Z", 0,
                   [("g00001", {}), ("a1b2c3", {}), ("G00001", None), ("g0001", None), ("g000011", None), ("g00001\n", None)])
    same_as_before("HOSTLIKE_RE", adguard_api.HOSTLIKE_RE, r"^[a-z0-9_]([a-z0-9_.-]{0,251}[a-z0-9_])?\Z", 0,
                   [("ads.example.com", {}), ("_dmarc.example.org", {}), ("x", {}), ("ads.example.com.", None), ("1.2.3.4", {}), ("a b", None), ("ADS.example", None)])
    same_as_before("RULE_RE", group_lists.RULE_RE,
                   r"""
    ^[ \t]*\|\|                                         # the rule covers the name and every name under it
    (?P<name>[a-z0-9_]([a-z0-9_.-]{0,251}[a-z0-9_])?)   # tracker.example.com
    \^[ \t]*\r?$                                        # the end of the name, and nothing after it
    """, re.VERBOSE | re.MULTILINE,
                   [("||tracker.example.com^", {"name": "tracker.example.com"}), ("  ||t.example^ \r", {"name": "t.example"}), ("||t.example^$third-party", None),
                    ("@@||t.example^", None), ("||*.example^", None), ("! ||t.example^", None)])
    ok([found["name"] for found in group_lists.RULE_RE.finditer("! list\n||a.example^\n||b.example^$important\n  ||c.example^\n")] == ["a.example", "c.example"], "RULE_RE: правила ищутся построчно во всём тексте")
    same_as_before("cascade IFACE_RE", cascade_models.IFACE_RE, r"[a-z][a-z0-9]{1,14}", 0,
                   [("awg0", {}), ("awgk1", {}), ("a", None), ("0awg", None), ("a" * 16, {})])
    ok(cascade_models.IFACE_RE.fullmatch("a" * 16) is None and cascade_models.IFACE_RE.fullmatch("a" * 15), "cascade IFACE_RE: целиком — не длиннее 15 знаков")
    same_as_before("cascade EXIT_NAME_RE", cascade_models.EXIT_NAME_RE, r"[A-Za-z0-9._-]{1,32}", 0, [("exit1-nl", {}), ("nl.2", {}), ("", None), (" x", None)])
    same_as_before("pauses ADDRESS_RE", pauses.ADDRESS_RE, r"^\d{1,3}(\.\d{1,3}){3}\Z", 0,
                   [("10.9.9.2", {}), ("10.9.9", None), ("10.9.9.2\n", None), ("1000.9.9.2", None), ("add cascade_susp 10.9.9.2", None)])
    same_as_before("spaces ADDRESS_RE", sets.ADDRESS_RE, r"^\d{1,3}(\.\d{1,3}){3}\Z", 0, [("172.16.17.5", {}), ("172.16.17.5/32", None)])
    same_as_before("DOMAIN_RE", entries.DOMAIN_RE,
                   r"""
    ^(?=.{1,253}$)                                          # the whole name is 1…253 characters
    (?P<first>[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)           # example
    (?P<rest>(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+)       # .com, .co.uk
    \Z
    """, re.VERBOSE,
                   [("example.com", {"first": "example", "rest": ".com"}), ("sub.example.co.uk", {"first": "sub", "rest": ".example.co.uk"}), ("example", None),
                    ("-x.com", None), ("x-.com", None), ("a" * 63 + ".com", {"first": "a" * 63}), ("a" * 64 + ".com", None), ("example.com.", None)])
    same_as_before("PATTERN_RE", entries.PATTERN_RE,
                   r"""
    ^(?=.{1,253}$)                                          # the whole pattern is 1…253 characters
    (?P<starred>[a-z0-9*-]+(\.[a-z0-9*-]+)*)                # api.*.cdn — labels that may hold stars
    \.(?P<domain>[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)        # .example — a plain label
    \.(?P<zone>[a-z]{2,24})                                 # .net — a plain zone
    \Z
    """, re.VERBOSE,
                   [("api.*.cdn.example.net", {"starred": "api.*.cdn", "domain": "example", "zone": "net"}), ("*cdn.example.org", {"starred": "*cdn", "domain": "example", "zone": "org"}),
                    ("*.example", None), ("x.*.net", None), ("*.example.n1", None)])
    same_as_before("NETWORK_RE", entries.NETWORK_RE,
                   r"""
    ^(?P<a>\d{1,3})\.(?P<b>\d{1,3})\.(?P<c>\d{1,3})\.(?P<d>\d{1,3})     # 149.154.160.0
    (/(?P<prefix>\d{1,2}))?                                             # /20
    \Z
    """, re.VERBOSE,
                   [("149.154.160.0/20", {"a": "149", "d": "0", "prefix": "20"}), ("91.108.4.1", {"a": "91", "d": "1", "prefix": None}), ("1.2.3.4/", None), ("1.2.3.4/123", None)])
    same_as_before("SCHEME_RE", entries.SCHEME_RE, r"^(https?://)?", 0,
                   [("https://example.com", {"scheme": "https://"}), ("http://example.com", {"scheme": "http://"}), ("example.com", {"scheme": None}), ("ftp://x", {"scheme": None})])
    ok(entries.SCHEME_RE.sub("", "https://example.com") == "example.com", "SCHEME_RE: снимается только схема")
    same_as_before("LEADING_STAR_RE", entries.LEADING_STAR_RE, r"^\*\.", 0, [("*.example.com", {}), ("example.com", None), ("a.*.example.com", None), ("**.x", None)])
    same_as_before("WORD_ZONE_RE", entries.WORD_ZONE_RE, r"\.(?P<zone>[a-z][a-z0-9-]*)$", 0, [(".com", {"zone": "com"}), ("example.com", None), (".1com", None)])
    ok(entries.WORD_ZONE_RE.search("пример.xn--p1ai")["zone"] == "xn--p1ai" and entries.WORD_ZONE_RE.search("10.20.0.0") is None, "WORD_ZONE_RE: зона-слово в конце имени, а не число")
    same_as_before("UP_TO_LAST_STAR_RE", routing.UP_TO_LAST_STAR_RE, r"^.*\*[^.]*\.", 0,
                   [("api.*.cdn.example.net", {}), ("*cdn.example.org", {}), ("example.com", None)])
    ok(routing.UP_TO_LAST_STAR_RE.sub("", "api.*.cdn.example.net") == "cdn.example.net" and routing.UP_TO_LAST_STAR_RE.sub("", "a.*.b*c.example.net") == "example.net",
       "UP_TO_LAST_STAR_RE: снимается всё до метки после последней звезды")
    same_as_before("IPSET_FILE_LINE_RE", routing.IPSET_FILE_LINE_RE, r"^  ipset_file: (?P<path>.*)$", re.MULTILINE,
                   [("  ipset_file: /old/place", {"path": "/old/place"}), (" ipset_file: /x", None), ("  ipset_file:/x", None), ("   ipset_file: /x", None), ("  ipset_file: ", {"path": ""})])
    settings = "server:\n  ipset_file: /old/place\n  port: 53\n"
    ok(routing.IPSET_FILE_LINE_RE.sub(lambda _: "  ipset_file: /new", settings) == "server:\n  ipset_file: /new\n  port: 53\n", "IPSET_FILE_LINE_RE: заменяется только эта строка настроек")
    same_as_before("ADDRESS_LINE_RE", routing.ADDRESS_LINE_RE, r"^(?P<address>\d+\.\d+\.\d+\.\d+)$", re.MULTILINE,
                   [("142.250.74.110", {"address": "142.250.74.110"}), ("cname.example.", None), ("1.2.3.4 ", None)])
    ok([found["address"] for found in routing.ADDRESS_LINE_RE.finditer("cdn.example.\n1.2.3.4\n5.6.7.8\n")] == ["1.2.3.4", "5.6.7.8"], "ADDRESS_LINE_RE: адреса из короткого ответа dig, имена пропущены")
    same_as_before("PROBE_PATH_RE", probe.PROBE_PATH_RE, r"""^(?P<path>/[A-Za-z0-9._~%!$&'()*+,;=:@/?-]{0,600})\Z""", 0,
                   [("/status?x=1", {"path": "/status?x=1"}), ("/", {"path": "/"}), ("/a b", None), ("/a#b", None), ("status", None), ("/ok\n", None)])
    same_as_before("LINK_TAIL_RE", lists.LINK_TAIL_RE, r"^(?P<tail>[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,400})\Z", 0,
                   [("/lists/friend.txt?v=2", {"tail": "/lists/friend.txt?v=2"}), ("", {"tail": ""}), ("/a b", None), ("/a\"b", None), ("/a#b", None), ("x" * 401, None)])
    same_as_before("BLOCK_RULE_RE", lists.BLOCK_RULE_RE, r"^\|\|(?P<name>[^\^/$]+)\^\Z", 0,
                   [("||example.com^", {"name": "example.com"}), ("||example.com^$third-party", None), ("|example.com^", None), ("||a/b^", None), ("||^", None)])


def t_services():
    print("\n— бот, панель, выходы, туннель")
    same_as_before("bot NAME_RE", bot_commands.NAME_RE, r"^[A-Za-z0-9_-]{1,32}$", 0, [("alice", {}), ("phone-7", {}), ("-x", {}), ("a b", None), ("a" * 33, None), ("alice\n", {})])
    same_as_before("bot TERM_RE", bot_commands.TERM_RE, r"^[1-9][0-9]{0,2}[hdw]$", 0, [("12h", {}), ("4w", {}), ("0h", None), ("12y", None)])
    same_as_before("exits IFACE_RE", exits_models.IFACE_RE, r"awgk[0-9]{1,2}", 0, [("awgk1", {}), ("awgk12", {}), ("awg0", None), ("xawgk1", None)])
    same_as_before("FONT_RE", web.FONT_RE, r"^[a-z0-9-]{1,60}\.woff2\Z", 0,
                   [("inter-latin.woff2", {}), ("Inter.woff2", None), ("x.ttf", None), ("../x.woff2", None), ("x.woff2\n", None)])
    same_as_before("VERSION_RE", panel_models.VERSION_RE, r"[0-9a-f]{12}", 0, [("0123456789ab", {}), ("0123456789AB", None), ("0123456789a", None)])
    ok(panel_models.VERSION_RE.fullmatch("0123456789abc") is None, "VERSION_RE: целиком — ровно 12 знаков")
    same_as_before("INVITE_ID_RE", invites.INVITE_ID_RE, r"[a-z0-9]{6}", 0, [("abc123", {}), ("ABC123", None), ("abc12", None)])
    same_as_before("TOP_JUMP_RE", firewall.TOP_JUMP_RE, r"-j (?P<chain>CASCADE-PEERS|DOCKER[A-Z-]*)$", 0,
                   [("-j DOCKER-USER", {"chain": "DOCKER-USER"}), ("-j CASCADE-PEERS", {"chain": "CASCADE-PEERS"}), ("-j  DOCKER", None), ("-j ACCEPT", None)])
    ok(firewall.TOP_JUMP_RE.search("-A FORWARD -i awg0 -o awg0 -j CASCADE-PEERS")["chain"] == "CASCADE-PEERS" and firewall.TOP_JUMP_RE.search("-A FORWARD -j DOCKER-USER")["chain"] == "DOCKER-USER",
       "TOP_JUMP_RE: прыжок в конце правила FORWARD")
    same_as_before("tunnel IFACE_RE", keeper.IFACE_RE, r"[a-z][a-z0-9]{0,14}", 0, [("awg0", {}), ("a", {}), ("0awg", None), ("Awg0", None)])


def t_kept_on_one_line():
    print("\n— выражения, оставленные в одну строку, остаются в одну строку")
    ok(commands.ID_RE.pattern == r"^[a-z0-9]{6}\Z" and commands.INVITE_RE.pattern == r"^[A-Za-z0-9_-]{22}\Z" and patterns.NAME_RE.pattern == r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z",
       "ID_RE, INVITE_RE, NAME_RE — прежним текстом")


# Kept on one line, as the owner decided: short anchored patterns, or the pattern is made of
# the command lines it stands for.
ONE_LINE = {id(commands.ID_RE), id(commands.INVITE_RE), id(patterns.NAME_RE), id(firewall.POSTUP_RE)}


def t_every_pattern():
    print("\n— каждое выражение пакета (кроме users/): re.VERBOSE, группы только именованные")
    package = Path(watch_lists.__file__).parents[1]
    found = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package.parent).with_suffix("")
        if relative.parts[1] == "users" or relative.name == "__main__":
            continue
        module = importlib.import_module(".".join(part for part in relative.parts if part != "__init__"))
        for name, value in vars(module).items():
            if isinstance(value, re.Pattern) and id(value) not in found:
                found[id(value)] = (f"{module.__name__}.{name}", value)
    ok(len(found) > 50 and ONE_LINE <= set(found), f"найдено {len(found)} выражений, среди них все оставленные в одну строку")
    for name, pattern in sorted(found.values(), key=lambda item: item[0]):
        if id(pattern) in ONE_LINE:
            continue
        ok(bool(pattern.flags & re.VERBOSE) and pattern.groups == len(pattern.groupindex), f"{name}: re.VERBOSE, группы именованные", (pattern.pattern, pattern.flags, pattern.groups))


def main():
    t_watch()
    t_core()
    t_agent()
    t_services()
    t_kept_on_one_line()
    t_every_pattern()
    print(f"\n{PASS} of {PASS + FAIL} checks passed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
