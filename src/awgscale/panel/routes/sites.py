"""Sites: what goes abroad, what is blocked, and how a name is routed right now."""
from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from awgscale.agent.commands import Command
from awgscale.core.patterns import HOST_RE, URL_PATH_RE

from ..agent import AgentAnswer
from ..arguments import packed
from ..deps import LOGGED_IN, MAY_CHANGE, Agent, Body, PanelJSON, relay
from ..models import BlockListRequest, BlockRequest, CountryRequest, ModeRequest, RefreshRequest, RuleRouteRequest, SitesRequest, as_sent

router = APIRouter(prefix="/api")


def checked_host(raw: str) -> str:
    host = raw.strip().lower().rstrip(".")
    if not HOST_RE.match(host):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "некорректное имя")
    return host


# ── reading ──────────────────────────────────────────────────────────────────

@router.get("/sites", dependencies=LOGGED_IN)
async def sites(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.SITES_GET, timeout=30)


@router.get("/block", dependencies=LOGGED_IN)
async def blocked(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.BLOCK_GET, timeout=30)


@router.get("/check", dependencies=LOGGED_IN)
async def check(agent: Agent, host: Annotated[str, Query()] = "") -> PanelJSON:
    """Where a name is routed."""
    return relay(await agent.call(Command.CHECK, checked_host(host), timeout=30))


@router.get("/probe", dependencies=LOGGED_IN)
async def probe(agent: Agent, host: Annotated[str, Query()] = "", path: Annotated[str, Query()] = "/") -> PanelJSON:
    """Whether the site opens directly and through each exit."""
    name = checked_host(host)
    if not URL_PATH_RE.match(path):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "некорректный адрес")
    return relay(await agent.call(Command.PROBE, name, path, timeout=45))


# ── changing ─────────────────────────────────────────────────────────────────

@router.post("/sites", dependencies=MAY_CHANGE)
async def save_sites(request: Annotated[SitesRequest, Depends(Body(SitesRequest, "некорректный список", limit=80000))], agent: Agent) -> PanelJSON:
    payload = {"base": request.version, "groups": as_sent(request.groups)}
    return relay(await agent.call(Command.SITES_SET, packed(payload), timeout=150))


@router.post("/rules/route", dependencies=MAY_CHANGE)
async def route_rule(request: Annotated[RuleRouteRequest, Depends(Body(RuleRouteRequest, "некорректные данные", limit=200))], agent: Agent) -> PanelJSON:
    """Where rules lead: abroad, blocked or off. A rule that moves between the sites and the blocking is saved in both."""
    payload = {"changes": [{"rule": change.rule, "route": change.route.value} for change in request.changes]}
    return relay(await agent.call(Command.RULE_ROUTE, packed(payload), timeout=280))


@router.post("/mode", dependencies=MAY_CHANGE)
async def set_mode(request: Annotated[ModeRequest, Depends(Body(ModeRequest, "некорректный режим"))], agent: Agent) -> PanelJSON:
    return relay(await agent.call(Command.MODE_SET, request.mode.value, timeout=240))


@router.post("/country", dependencies=MAY_CHANGE)
async def set_country(request: Annotated[CountryRequest, Depends(Body(CountryRequest, "некорректный код страны"))], agent: Agent) -> PanelJSON:
    """The home country: whose networks go direct. Its list is downloaded while the change applies."""
    return relay(await agent.call(Command.COUNTRY_SET, request.country, timeout=240))


@router.post("/sites/refresh", dependencies=MAY_CHANGE)
async def refresh_lists(request: Annotated[RefreshRequest, Depends(Body(RefreshRequest, "некорректные данные", limit=200))], agent: Agent) -> PanelJSON:
    """Download a list by link again, or every list that is on."""
    which = [request.id] if request.id else []
    return relay(await agent.call(Command.SITES_REFRESH, *which, timeout=150))


@router.post("/block", dependencies=MAY_CHANGE)
async def save_blocked(request: Annotated[BlockRequest, Depends(Body(BlockRequest, "некорректный список", limit=80000))], agent: Agent) -> PanelJSON:
    payload = {"groups": as_sent(request.groups), "lists": request.lists, "safebrowsing": bool(request.safebrowsing)}
    return relay(await agent.call(Command.BLOCK_SET, packed(payload), timeout=120))


@router.post("/block/lists", dependencies=MAY_CHANGE)
async def change_block_lists(request: Annotated[BlockListRequest, Depends(Body(BlockListRequest, "некорректные данные", limit=2000))], agent: Agent) -> PanelJSON:
    """Block lists by link: add, remove, rename, refresh."""
    return relay(await agent.call(Command.BLOCK_LISTS, packed(request.for_agent()), timeout=150))
