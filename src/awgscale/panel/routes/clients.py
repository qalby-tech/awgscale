"""Users: making and removing them, their terms, pauses and exits, their groups and spaces, their connection codes."""
from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from awgscale.agent.commands import AUTO_EXIT, NO_TERM, Command, Switch

from ..agent import AgentAnswer
from ..arguments import checked_name, packed
from ..deps import LOGGED_IN, MAY_CHANGE, Agent, Body, PanelJSON, State, relay
from ..models import AddClientRequest, ClientsParams, ExpireRequest, NamesRequest, SpacesRequest, SuspendRequest, UserExitRequest, UserGroupsRequest, as_sent

router = APIRouter(prefix="/api")

# ── reading ──────────────────────────────────────────────────────────────────

@router.get("/clients", dependencies=LOGGED_IN)
async def clients(params: Annotated[ClientsParams, Query()], board: State) -> PanelJSON:
    """One page of users: searched, filtered and ordered by the agent; at most 100 at a time."""
    page = await board.clients(packed(params.for_agent()))
    return PanelJSON(page.wire(), HTTPStatus.OK if page.ok else HTTPStatus.BAD_GATEWAY)


@router.get("/ugroups", dependencies=LOGGED_IN)
async def user_groups(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.UGROUPS_GET, timeout=30)


@router.get("/spaces", dependencies=LOGGED_IN)
async def spaces(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.SPACES_GET, timeout=30)


@router.get("/diag", dependencies=LOGGED_IN)
async def diagnose(agent: Agent, name: Annotated[str, Query()] = "") -> PanelJSON:
    return relay(await agent.call(Command.DIAG, checked_name(name), timeout=40))


@router.get("/clients/{name:path}/qr", dependencies=LOGGED_IN)
async def connection_code(name: str, agent: Agent) -> AgentAnswer:
    return await agent.call(Command.QR, checked_name(name), timeout=30)


# ── changing ─────────────────────────────────────────────────────────────────

@router.post("/clients", dependencies=MAY_CHANGE)
async def add_client(
    request: Annotated[AddClientRequest, Depends(Body(AddClientRequest, "имя: латиница, цифры, _ и -, до 32 символов", by_field={"expires": "срок: например 12h, 7d, 4w"}))],
    agent: Agent,
) -> PanelJSON:
    term = [request.expires] if request.expires else []
    return relay(await agent.call(Command.ADD, request.name, *term), HTTPStatus.BAD_GATEWAY)


@router.post("/clients/remove", dependencies=MAY_CHANGE)
async def remove_clients(request: Annotated[NamesRequest, Depends(Body(NamesRequest, "некорректные данные", limit=4000))], agent: Agent) -> PanelJSON:
    """Several users at once (a whole group)."""
    return relay(await agent.call(Command.REMOVE, *request.names, timeout=170), HTTPStatus.BAD_GATEWAY)


@router.post("/clients/expire", dependencies=MAY_CHANGE)
async def expire_clients(
    request: Annotated[ExpireRequest, Depends(Body(ExpireRequest, "некорректные данные", limit=4000, by_field={"expires": "срок: например 12h, 7d, 4w"}))],
    agent: Agent,
) -> PanelJSON:
    """A new term for several users, or none."""
    return relay(await agent.call(Command.EXPIRE, request.expires or NO_TERM, *request.names, timeout=90), HTTPStatus.BAD_GATEWAY)


@router.post("/clients/suspend", dependencies=MAY_CHANGE)
async def suspend_clients(request: Annotated[SuspendRequest, Depends(Body(SuspendRequest, "некорректные данные", limit=4000))], agent: Agent) -> PanelJSON:
    """Pause or resume users without removing them."""
    return relay(await agent.call(Command.SUSPEND, Switch.ON if request.on else Switch.OFF, *request.names, timeout=60))


@router.post("/clients/exit", dependencies=MAY_CHANGE)
async def keep_on_exit(request: Annotated[UserExitRequest, Depends(Body(UserExitRequest, "некорректные данные", limit=4000))], agent: Agent) -> PanelJSON:
    """Keep users on one exit by hand, or let the group and the shares decide again."""
    exit_ = AUTO_EXIT if request.exit is None else str(request.exit)
    return relay(await agent.call(Command.USER_EXIT, exit_, *request.names, timeout=90))


@router.delete("/clients/{name:path}", dependencies=MAY_CHANGE)
async def remove_client(name: str, agent: Agent) -> PanelJSON:
    return relay(await agent.call(Command.REMOVE, checked_name(name)), HTTPStatus.BAD_GATEWAY)


@router.post("/ugroups", dependencies=MAY_CHANGE)
async def save_user_groups(request: Annotated[UserGroupsRequest, Depends(Body(UserGroupsRequest, "некорректные данные", limit=40000))], agent: Agent) -> PanelJSON:
    payload = {"base": request.version, "groups": [group.for_agent() for group in request.groups]}
    return relay(await agent.call(Command.UGROUPS_SET, packed(payload), timeout=120))


@router.post("/spaces", dependencies=MAY_CHANGE)
async def save_spaces(request: Annotated[SpacesRequest, Depends(Body(SpacesRequest, "некорректный список", limit=40000))], agent: Agent) -> PanelJSON:
    payload = {"base": request.version, "spaces": as_sent(request.spaces)}
    return relay(await agent.call(Command.SPACES_SET, packed(payload), timeout=120))
