#!/usr/bin/env python3
"""The home country (HOME_COUNTRY in cascade.conf): whose networks go direct and what
"everything foreign" means. Russia unless said otherwise.

What is checked: a server that does not name it is Russian and its document and file
stay what they were; another country is taken, compiled, read back and read by the
watch service; a wrong code is refused by both; the list is downloaded for that
country and kept in its own file; the day's tops count that country's domains as home.

    uv run python tests/test_home_country.py
One line per check, the last line is "N of M checks passed".
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from awgscale.agent.cascade.conf import parse, text_of  # noqa: E402
from awgscale.agent.cascade.models import checked  # noqa: E402
from awgscale.agent.commands import RouteMode  # noqa: E402
from awgscale.agent.tops.service import RUSSIAN_ZONES, Way, national_zones, route_by  # noqa: E402
from awgscale.service.errors import Refused  # noqa: E402
from awgscale.watch import lists  # noqa: E402
from awgscale.watch.conf import CascadeConf  # noqa: E402
from awgscale.watch.places import Places  # noqa: E402

SEED = Path(ROOT, "cascade", "cascade.conf").read_text()
CASCADE = Path(ROOT, "tests", "cascade.conf").read_text()
KZ_NETS = "".join(f"2.{n}.0.0/16\n" for n in range(30))

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
    try:
        checked(document)
    except ValueError as e:
        return str(e)
    return ""


print("\n— Russia unless said otherwise")
seed = parse(SEED)
ok(seed.home_country == "ru", "the seed names Russia", seed.home_country)
cascade = checked(parse(CASCADE).wire())
ok(cascade.home_country == "ru" and "home_country" not in cascade.wire(), "a file without HOME_COUNTRY is Russian, and its document has no trace of it")
ok("HOME_COUNTRY" not in text_of(cascade), "…nor the file compiled from it")
conf = watch_load(CASCADE)
ok(isinstance(conf, CascadeConf) and conf.home_country == "ru", "the watch service reads it as Russian", conf)

print("\n— another country")
kazakh = checked(dict(cascade.wire(), home_country="kz"))
ok(kazakh.home_country == "kz" and kazakh.wire().get("home_country") == "kz", "the agent takes kz and shows it")
text = text_of(kazakh)
ok('HOME_COUNTRY="kz"\n' in text and parse(text) == kazakh, "the file says so and reads back the same", text[-160:])
conf = watch_load(text)
ok(isinstance(conf, CascadeConf) and conf.home_country == "kz", "the watch service reads kz", conf)

print("\n— a wrong code is refused, by both")
for wrong in ("KZ", "kaz", "k", 7):
    ok("home_country" in agent_check(dict(cascade.wire(), home_country=wrong)), f"agent: {wrong!r} is refused")
ok(isinstance(watch_load(CASCADE + 'HOME_COUNTRY="Kazakhstan"\n'), str), "watch: HOME_COUNTRY=\"Kazakhstan\" is refused")

print("\n— the list of the country")
ok(lists.zone_urls("ru") == (lists.ZONE_URL.format(country="ru"), lists.RU_ZONE_MIRROR), "Russia: ipdeny, then the mirror")
ok(lists.zone_urls("kz") == ("https://www.ipdeny.com/ipblocks/data/aggregated/kz-aggregated.zone",), "kz: its ipdeny list only")
ok(lists.zone_valid(KZ_NETS, "kz"), "thirty networks are a list for kz")
ok(not lists.zone_valid(KZ_NETS, "ru"), "…but not for Russia, which has thousands")
ok(not lists.zone_valid("<html>blocked</html>\n", "kz"), "a stub page is not a list")
with tempfile.TemporaryDirectory() as directory:
    places = Places(dir=Path(directory), run=Path(directory, "run"))
    ok(places.home_zone("ru").name == "ru.zone", "Russia's list stays in ru.zone, where a running server has it")
    asked: list[str] = []
    real_fetch = lists.fetch
    lists.fetch = lambda url, source=None, timeout=30: asked.append(url) or KZ_NETS
    try:
        got = lists.fetch_home_zone(places, "kz")
    finally:
        lists.fetch = real_fetch
    ok(got and places.home_zone("kz").read_text() == KZ_NETS and not places.home_zone("ru").exists(),
       "kz's list is downloaded into kz.zone, Russia's is not touched", asked)

print("\n— the day's tops")
ok(national_zones(CASCADE) == RUSSIAN_ZONES and national_zones(None) == RUSSIAN_ZONES, "Russia: .ru, .su, .рф")
ok(national_zones(text) == ("kz",), "kz: .kz", national_zones(text))
route = route_by(RouteMode.FOREIGN, set(), national_zones(text))
ok(route("egov.kz") is Way.DIRECT and route("yandex.ru") is Way.EXIT, "everything foreign from Kazakhstan: .kz direct, .ru abroad")

print(f"\n{passed} of {passed + failed} checks passed")
sys.exit(1 if failed else 0)
