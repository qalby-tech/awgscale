"""Where an exit is: its country, found by the exit's public address (RIPEstat).

Only the exit's public address leaves the server; a lookup that does not answer
in time is no country, never an error. The guess an exit's name gives is only
shown (ExitSetting.shown_country), never stored."""
from __future__ import annotations

import asyncio
import ipaddress
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..clients.http import ERROR_LENGTH
from ..iso_countries import is_country
from ..ports import TunnelFacts
from ..tunnel import endpoint_host

log = logging.getLogger("awgscale.agent")


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
    """The country of an exit: by the public address its link points to."""

    def __init__(self, lookup: RipeStat, tunnel: TunnelFacts, deadline: float) -> None:
        self.__lookup = lookup
        self.__tunnel = tunnel
        self.__deadline = deadline

    async def __by_address(self, iface: str) -> str | None:
        address = endpoint_host(await self.__tunnel.endpoint_of(iface))
        return await self.__lookup.country_of(address) if address is not None else None

    async def find(self, name: str, iface: str) -> str | None:
        """Never raises and never takes longer than the deadline: no answer in time is no country."""
        try:
            return await asyncio.wait_for(self.__by_address(iface), self.__deadline)
        except Exception as e:
            log.warning("the country of exit %s not found by its address: %s", name, repr(e)[:ERROR_LENGTH])
            return None


class NoLookup:
    """The lookup switched off (VPN_COUNTRY_LOOKUP_URL empty): nothing leaves the server, no country is found."""

    async def find(self, name: str, iface: str) -> str | None:
        return None
