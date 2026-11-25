"""The network as its owner sees it in the app: one answer made of what the panel already knows (the last look at
the cascade) and what the agent keeps (the rules, the groups of people, the exits' countries).

It is a summary, not the stores themselves: names and counts, never a user's key, endpoint or connection code."""
from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, Field

from awgscale.agent.commands import RuleRoute
from awgscale.agent.rules.service import route_of
from awgscale.agent.rules.store import Way, way_of
from awgscale.agent.sites.entries import is_network
from awgscale.watch.conf import RouteMode

from .looks import ExitLook, PanelState

PERCENT = 100


class OwnerExit(BaseModel):
    index: int = Field(description="The exit's number")
    name: str = Field(description="The exit's name")
    state: str = Field(description="up, down or unknown")
    country: str | None = Field(default=None, description="Its country, two letters, when known")
    weight: int = Field(default=0, ge=0, description="Its share of new users as stored (the base of a change)")
    share: int = Field(default=0, ge=0, le=PERCENT, description="…in percent of all exits")
    users: int = Field(default=0, ge=0, description="Users on it now")
    online: int = Field(default=0, ge=0, description="…of them online")


class OwnerRule(BaseModel):
    id: str = Field(description="The rule's id")
    name: str = Field(description="What the admin called it")
    route: RuleRoute = Field(description="Where it leads now; off: as if it were not there")
    way: Way = Field(description="Where it leads when it is on (a rule switched off keeps it)")
    items: int = Field(default=0, ge=0, description="How many sites it names")
    by_link: bool = Field(default=False, description="A list by link: its sites are the file's")
    movable: bool = Field(default=True, description="It may go directly or be blocked: it names sites only, written by hand")
    groups: list[str] = Field(default_factory=list, description="The names of the groups of people it is for; empty: everybody")


class OwnerGroup(BaseModel):
    id: str | None = Field(default=None, description="The group's id; none: the people in no group")
    name: str = Field(description="Its name")
    filter: str | None = Field(default=None, description="How its members' requests are filtered")
    space: bool = Field(default=False, description="A device space of exactly its members")
    users: int = Field(default=0, ge=0, description="Members")
    online: int = Field(default=0, ge=0, description="…of them online")


class OwnerTraffic(BaseModel):
    """The day's lookups by where they went: what the mini map of the app draws."""

    exit: int = Field(default=0, ge=0, description="Lookups that went abroad")
    direct: int = Field(default=0, ge=0, description="…directly")
    block: int = Field(default=0, ge=0, description="…were blocked")


class OwnerNetwork(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    mode: RouteMode = Field(default=RouteMode.LIST, description="What goes abroad")
    single_server: bool = Field(default=False, description="A server without exits")
    users: int = Field(default=0, ge=0, description="Users in all")
    online: int = Field(default=0, ge=0, description="…online now")
    traffic: OwnerTraffic = Field(default_factory=OwnerTraffic, description="The day's lookups by where they went")
    exits: list[OwnerExit] = Field(default_factory=list, description="The exits in their order")
    rules: list[OwnerRule] = Field(default_factory=list, description="The rules in the order that decides")
    rules_v: str = Field(default="", description="The version of the rules: a change based on another one is refused")
    groups: list[OwnerGroup] = Field(default_factory=list, description="The groups of people, then those in none")
    stale: bool = Field(default=False, description="The last look at the cascade is old: a later one failed")


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _exit_of(look: ExitLook, whole: int, countries: dict[int, str]) -> OwnerExit | None:
    if look.index is None:
        return None
    weight = _number(look.weight)
    return OwnerExit(
        index=look.index,
        name=look.name or f"exit{look.index}",
        state=look.state or "unknown",
        country=countries.get(look.index),
        weight=weight,
        share=round(weight * PERCENT / whole) if whole else 0,
        users=look.users or 0,
        online=look.online or 0,
    )


def _rule_of(rule: dict[str, Any], group_names: dict[str, str]) -> OwnerRule | None:
    rule_id = rule.get("id")
    if not isinstance(rule_id, str):
        return None
    items = [entry for entry in rule.get("items") or [] if isinstance(entry, str)]
    by_link = bool(rule.get("url"))
    scope = [group for group in rule.get("groups") or [] if isinstance(group, str)]
    return OwnerRule(
        id=rule_id,
        name=str(rule.get("name") or ""),
        route=route_of(rule),
        way=way_of(rule),
        items=len(items),
        by_link=by_link,
        movable=not by_link and not rule.get("auto") and not any(is_network(entry) for entry in items),
        groups=[group_names.get(group, group) for group in scope],
    )


def _counts_of(state: PanelState) -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
    """→ {group id: (members, online)} and the same for the people in no group, as the last look counted them."""
    users = state.users.model_dump() if state.users is not None else {}
    by_group = {
        str(count.get("id")): (_number(count.get("users")), _number(count.get("online")))
        for count in users.get("groups") or [] if isinstance(count, dict)
    }
    held = users.get("ungrouped")
    ungrouped: dict[str, Any] = held if isinstance(held, dict) else {}
    return by_group, (_number(ungrouped.get("users")), _number(ungrouped.get("online")))


def network_of(state: PanelState, rules: dict[str, Any], groups: dict[str, Any], exits: dict[str, Any]) -> OwnerNetwork:
    """The summary from the last look (`state`) and the agent's answers of rules-get, ugroups-get and app-exits.
    An answer that failed leaves its part empty: the rest is still shown. The people in no group come last, unnamed
    (the app names them in its own language)."""
    stored_groups: Sequence[dict[str, Any]] = [group for group in groups.get("groups") or [] if isinstance(group, dict)] if groups.get("ok", True) else []
    group_names = {str(group.get("id")): str(group.get("name") or "") for group in stored_groups if group.get("id")}
    countries = {
        each["index"]: each["country"]
        for each in exits.get("exits") or [] if isinstance(each, dict) and isinstance(each.get("index"), int) and isinstance(each.get("country"), str)
    }
    looks = state.exits or []
    whole = sum(_number(look.weight) for look in looks)
    by_group, ungrouped = _counts_of(state)
    shown_groups = [
        OwnerGroup(
            id=str(group.get("id")),
            name=str(group.get("name") or ""),
            filter=group.get("filter") if isinstance(group.get("filter"), str) else None,
            space=bool(group.get("space")),
            users=by_group.get(str(group.get("id")), (len(group.get("members") or []), 0))[0],
            online=by_group.get(str(group.get("id")), (0, 0))[1],
        )
        for group in stored_groups if group.get("id")
    ]
    if ungrouped[0]:
        shown_groups.append(OwnerGroup(id=None, name="", users=ungrouped[0], online=ungrouped[1]))
    routing = state.routing
    dns = state.dns
    try:
        mode = RouteMode(routing.mode) if routing is not None and routing.mode else RouteMode.LIST
    except ValueError:
        mode = RouteMode.LIST
    stored_rules = [rule for rule in rules.get("rules") or [] if isinstance(rule, dict)] if rules.get("ok", True) else []
    return OwnerNetwork(
        mode=mode,
        single_server=bool(routing is not None and routing.single_server),
        users=state.users.users if state.users is not None else 0,
        online=state.users.online if state.users is not None else 0,
        traffic=OwnerTraffic(
            exit=_number(dns.top_exit_total) if dns is not None else 0,
            direct=_number(dns.top_direct_total) if dns is not None else 0,
            block=_number(dns.blocked) if dns is not None else 0,
        ),
        exits=[shown for shown in (_exit_of(look, whole, countries) for look in looks) if shown is not None],
        rules=[shown for shown in (_rule_of(rule, group_names) for rule in stored_rules) if shown is not None],
        rules_v=str(rules.get("v") or "") if rules.get("ok", True) else "",
        groups=shown_groups,
        stale=bool(state.stale),
    )


JOURNAL_SHOWN = 200


class OwnerNote(BaseModel):
    """One line of the change journal: never more than the panel's own page shows."""

    t: float = Field(ge=0, description="When, Unix seconds as the agent wrote them")
    text: str = Field(description="What was changed")
    bad: bool = Field(default=False, description="It failed")
    who: str | None = Field(default=None, description="Who did it: panel, bot, or the system itself")


class OwnerJournal(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    items: list[OwnerNote] = Field(default_factory=list, description="The latest first")


def journal_of(answer: dict[str, Any]) -> OwnerJournal:
    """The change journal as the app shows it; a line that does not fit is left out."""
    notes: list[OwnerNote] = []
    for item in answer.get("items") or []:
        if len(notes) >= JOURNAL_SHOWN:
            break
        try:
            notes.append(OwnerNote.model_validate({key: item[key] for key in ("t", "text", "bad", "who") if isinstance(item, dict) and key in item}))
        except ValueError:
            continue
    return OwnerJournal(items=notes)
