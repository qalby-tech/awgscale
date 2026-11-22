"""What the page is shown about blocking, and what it may ask to change."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ..adguard.models import GROUP_LISTS_FILE, GROUP_LISTS_TITLE, THREATS_LIST_TITLE, THREATS_LIST_URL, TopBlocked
from ..store.documents import Record

MAX_BLOCK_LISTS = 8
BLOCK_KIND = "block"
ALLOW_MARK = "allow"
SAFE_BROWSING_TITLE = "Опасные сайты"
# names shown in the panel for AdGuard's own block lists; any other list was attached by the admin by link
LIST_TITLES: Mapping[str, str] = {
    "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt": "Реклама",
    "https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt": "AdAway",
    GROUP_LISTS_FILE: GROUP_LISTS_TITLE,          # made by the agent while a group needs a list of its own (adguard.group_lists)
    THREATS_LIST_URL: THREATS_LIST_TITLE,         # for everybody, put in by the agent when it is missing
}


class ListChange(StrEnum):
    """What block-lists may be asked to do."""

    ADD = "add"
    REMOVE = "remove"
    RENAME = "rename"
    REFRESH = "refresh"


def is_allow_group(group: object) -> bool:
    """There used to be an «Исключения» group of allow rules; the idea was dropped, and such a group is ignored wherever it turns up."""
    return isinstance(group, dict) and (group.get("id") == ALLOW_MARK or group.get("kind") == ALLOW_MARK)


class BlockGroup(BaseModel):
    """A block group as it is stored and shown: an earlier version's `auto`
    mark is kept as it was, and is left out when the group has none."""

    id: str = Field(description="The group's own id, kept while the group exists")
    name: str = Field(description="What the admin called it")
    on: bool = Field(description="Its names are blocked now")
    items: list[str] = Field(description="Names and patterns, put in order")
    auto: Any = Field(default=None, description="Left over from an earlier version; carried along untouched")
    kind: str = Field(description="What the group does: block")

    def record(self) -> Record:
        return self.model_dump(exclude={"auto"} if not self.auto else None)


class ListShown(BaseModel):
    """One of AdGuard's block lists as the panel shows it."""

    model_config = ConfigDict(frozen=True)

    id: Any = Field(description="AdGuard's number of the list")
    title: Any = Field(description="The name shown in the panel")
    name: Any = Field(description="How AdGuard calls it")
    url: str = Field(description="Where it is downloaded from")
    custom: bool = Field(description="Attached by the admin by link: can be renamed and removed")
    on: bool = Field(description="The list is switched on")
    rules: int = Field(description="How many rules it has")
    updated: Any = Field(description="When AdGuard last downloaded it")
    day: int = Field(description="Requests the list stopped in the last day")


class SafeBrowsingShown(BaseModel):
    model_config = ConfigDict(frozen=True)

    on: bool = Field(description="The dangerous-sites check is on")
    day: int = Field(description="Requests it stopped in the last day")


class BlockShown(BaseModel):
    """The answer of block-get, and of every change of the blocking."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    lists: list[ListShown] = Field(description="AdGuard's block lists")
    max_lists: int = Field(default=MAX_BLOCK_LISTS, description="How many lists the admin may attach by link")
    safebrowsing: SafeBrowsingShown = Field(description="The dangerous-sites check")
    groups: list[Record] = Field(description="The admin's own groups of blocked names, as stored")
    v: str = Field(default="", description="The fingerprint of the groups: a save based on another one is refused")
    covered: dict[str, dict[str, str]] = Field(default_factory=dict, description="Entries a rule above already names: {group id: {entry: that rule's name}}")
    top: list[TopBlocked] = Field(description="The names stopped most often today — totals, never who asked")
    queries: Any = Field(description="Requests in all today")
    blocked: Any = Field(description="Requests stopped today")

    def wire(self) -> dict[str, Any]:
        shown = self.model_dump()
        if not shown["covered"]:
            del shown["covered"]                # absent when nothing is covered: the answer is as it was
        return shown
