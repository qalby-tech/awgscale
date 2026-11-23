"""Where an exit is: its country, found by the exit's public address or, failing that, by its name.

Only the exit's public address leaves the server (to RIPEstat); a lookup that
does not answer in time is no country, never an error."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..ports import TunnelFacts
from ..tunnel import endpoint_host

log = logging.getLogger("awgscale.agent")

ERROR_LENGTH = 200
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
    """exit1-nl → nl; a name without a known code at its end → None."""
    found = NAME_COUNTRY_RE.search(name)
    if found is None:
        return None
    code = found["code"].lower()
    return code if is_country(code) else None


# ── RIPEstat's answer ────────────────────────────────────────────────────────

class GeoLocation(BaseModel):
    country: str = Field(default="", description="ISO 3166-1 alpha-2, upper case; empty when unknown")
    covered_percentage: float = Field(default=0.0, description="How much of the asked resource is there")


class LocatedResource(BaseModel):
    locations: list[GeoLocation] = Field(default_factory=list, description="Where the parts of the resource are")


class GeoData(BaseModel):
    located_resources: list[LocatedResource] = Field(default_factory=list, description="The resources asked")


class GeoAnswer(BaseModel):
    """maxmind-geo-lite: {"data": {"located_resources": [{"locations": [{"country": "NL", "covered_percentage": 100.0}]}]}}"""

    data: GeoData = Field(description="What was found")

    def country(self) -> str | None:
        """The country that covers most of the address; None when there is none."""
        locations = [location for resource in self.data.located_resources for location in resource.locations]
        if not locations:
            return None
        code = max(locations, key=lambda location: location.covered_percentage).country.lower()
        return code if is_country(code) else None


class RipeStat:
    """RIPEstat's geolocation (MaxMind GeoLite) of one public address."""

    def __init__(self, url: str, timeout: float, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.__url = url
        self.__timeout = timeout
        self.__transport = transport

    async def ask(self, address: str) -> str | None:
        """The country of the address, as RIPEstat has it; None when it does not answer in time or knows none."""
        try:
            async with httpx.AsyncClient(timeout=self.__timeout, transport=self.__transport) as client:
                response = await client.get(self.__url, params={"resource": address})
            response.raise_for_status()
            return GeoAnswer.model_validate_json(response.content).country()
        except (httpx.HTTPError, ValidationError, ValueError) as e:
            log.warning("the country of an exit not found at RIPEstat: %s", repr(e)[:ERROR_LENGTH])
            return None

    async def country_of(self, address: str) -> str | None:
        """Only a public address is ever asked about."""
        try:
            public = ipaddress.ip_address(address).is_global
        except ValueError:
            return None
        return await self.ask(address) if public else None


class ExitCountries:
    """The country of an exit: by the public address its link points to, else by the end of its name."""

    def __init__(self, lookup: RipeStat, tunnel: TunnelFacts, deadline: float) -> None:
        self.__lookup = lookup
        self.__tunnel = tunnel
        self.__deadline = deadline

    async def __by_address(self, iface: str) -> str | None:
        address = endpoint_host(await self.__tunnel.endpoint_of(iface))
        return await self.__lookup.country_of(address) if address is not None else None

    async def find(self, name: str, iface: str) -> str | None:
        """Never raises and never takes longer than the deadline: no answer in time is the name's country, or none."""
        try:
            found = await asyncio.wait_for(self.__by_address(iface), self.__deadline)
        except Exception as e:
            log.warning("the country of exit %s not found by its address: %s", name, repr(e)[:ERROR_LENGTH])
            found = None
        return found or country_in_name(name)
