"""What AdGuard says and takes, as objects."""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

# HaGeZi's lists in AdBlock syntax: the levels, the children's add-on lists, the threats
HAGEZI_URL = "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/"
# the groups' own lists, made by the agent into one local file AdGuard reads
# (its safe_fs_patterns allow /opt/AdGuardHome/userfilters/*)
GROUP_LISTS_FILE = "/opt/AdGuardHome/userfilters/group-lists.txt"
GROUP_LISTS_TITLE = "Списки групп"
# Threat Intelligence Feeds (mini), for everybody: an ordinary list by link
THREATS_LIST_URL = HAGEZI_URL + "tif.mini.txt"
THREATS_LIST_TITLE = "Угрозы"


class FilterList(BaseModel):
    """One of AdGuard's block lists, as it reports it."""

    model_config = ConfigDict(frozen=True)

    id: Any = Field(default=None, description="AdGuard's number of the list")
    url: str = Field(default="", description="Where the list is downloaded from")
    name: Any = Field(default=None, description="How AdGuard calls it")
    enabled: bool = Field(default=False, description="The list is switched on")
    rules_count: int = Field(default=0, description="How many rules it has; AdGuard says 0 for a list that is off")
    last_updated: Any = Field(default=None, description="When AdGuard last downloaded it, in its own words")

    @classmethod
    def of(cls, record: Mapping[str, Any]) -> FilterList:
        return cls(
            id=record.get("id"),
            url=str(record.get("url") or ""),
            name=record.get("name"),
            enabled=bool(record.get("enabled")),
            rules_count=record.get("rules_count") or 0,
            last_updated=record.get("last_updated") or None,
        )


class TopBlocked(BaseModel):
    """A name the blocker stopped, and how many times. Totals only: never who asked."""

    model_config = ConfigDict(frozen=True)

    domain: str = Field(description="The name")
    count: Any = Field(description="How many requests for it were stopped")


class ClientCount(BaseModel):
    """An address in the tunnel and how many requests it made. A number: never what was asked."""

    model_config = ConfigDict(frozen=True)

    ip: str = Field(description="The address")
    count: Any = Field(description="Its requests of the day")


class Counters(BaseModel):
    """AdGuard's counters of the day."""

    model_config = ConfigDict(frozen=True)

    queries: Any = Field(default=0, description="Requests in all")
    blocked: Any = Field(default=0, description="Requests stopped by the lists and the admin's own rules")
    safe: Any = Field(default=0, description="Requests stopped as dangerous sites")
    top_blocked: tuple[TopBlocked, ...] = Field(default=(), description="The names stopped most often")
    average_seconds: Any = Field(default=0, description="How long an answer takes on average, seconds")
    top_clients: tuple[ClientCount, ...] = Field(default=(), description="The most active addresses")


class Profile(BaseModel):
    """AdGuard's settings for the members of one group that is filtered in its own way: a "client" of AdGuard, known by their tunnel addresses."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        description='"grp-" and the group\'s id, or "grp-" and a level (the members of every group of that level); '
        '"-all" after it for those of them who send all their traffic through their exit, "grp-all" for such people filtered as everybody'
    )
    ids: list[str] = Field(description="Tunnel addresses of the members, sorted")
    use_global_settings: bool = Field(default=False, description="False: the group has settings of its own; true only for \"grp-all\"")
    filtering_enabled: bool = Field(description="The block lists apply")
    parental_enabled: bool = Field(description="Adult sites are stopped")
    safebrowsing_enabled: bool = Field(description="Dangerous sites are stopped")
    safe_search: dict[str, bool] = Field(description="Safe search, and for which search engines")
    use_global_blocked_services: bool = Field(default=True, description="Always true: no services are blocked per group")
    blocked_services: list[str] = Field(default_factory=list, description="Always empty")
    upstreams: list[str] = Field(
        default_factory=list,
        description="Empty: as everybody (the network's mode decides). For those who send all their traffic through their exit, its resolvers",
    )
    upstreams_cache_enabled: bool = Field(default=False, description="Answers of the client's own resolvers are kept (the global cache is not theirs)")
    upstreams_cache_size: int = Field(default=0, description="How much of them, bytes")
    tags: list[str] = Field(
        default_factory=list,
        description="The tags of the groups' lists its people have (adguard.group_lists.ListTag), sorted, each once: AdGuard reads them back so",
    )
    ignore_querylog: bool = Field(default=False, description="Always false")
    ignore_statistics: bool = Field(default=False, description="Always false")

    @field_validator("tags")
    @classmethod
    def sorted_once(cls, tags: list[str]) -> list[str]:
        """AdGuard reads a client's tags back sorted and keeps a tag sent twice: they are always sent sorted, each once."""
        return sorted(set(tags))

    def wire(self) -> dict[str, Any]:
        """As AdGuard takes it; the cache of the client's own resolvers only for a client that has them."""
        return self.model_dump(exclude=None if self.upstreams_cache_enabled else {"upstreams_cache_enabled", "upstreams_cache_size"})
