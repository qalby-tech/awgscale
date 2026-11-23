"""Countries by their ISO 3166-1 alpha-2 codes, and the guess an exit's name gives.

A leaf: the settings' check, the lookup and the answers all read the same list."""
from __future__ import annotations

import re

# ISO 3166-1 alpha-2, lower case: every country and territory that has a code
ISO_COUNTRIES = frozenset("""
    ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw by bz
    ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi fj fk fm fo fr
    ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in io iq ir is it je jm jo jp
    ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm mn mo mp mq mr ms mt
    mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw
    sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug
    um us uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw
""".split())
# A country at the end of an exit's name, after a separator:   exit1-nl   nl.de   exit_fi
NAME_COUNTRY_RE = re.compile(
    r"""
    [-._]                  # -
    (?P<code>[A-Za-z]{2})  # nl
    \Z
    """,
    re.VERBOSE,
)


def is_country(code: object) -> bool:
    """A known ISO 3166-1 alpha-2 code, lower case."""
    return isinstance(code, str) and code in ISO_COUNTRIES


def country_in_name(name: str) -> str | None:
    """exit1-nl → nl; a name without a known code at its end → None. Only shown, never stored."""
    found = NAME_COUNTRY_RE.search(name)
    if found is None:
        return None
    code = found["code"].lower()
    return code if is_country(code) else None
