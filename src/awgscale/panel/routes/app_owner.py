"""The owner's part of the app: the network read and changed from a device of an owner.

Whose device asks is said by its tunnel address (routes/app.py); whether that user owns the network is asked of the
agent on every request (app-me), so a user who stopped being an owner loses these paths at once. An agent that cannot
be asked refuses them too. What is shown is a summary: no user's key, endpoint or connection code."""
from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from awgscale.agent.commands import Command
from awgscale.service.qr import qr_png

from ..arguments import packed
from ..deps import Agent, Body, PanelJSON, State, relay
from ..models import InviteCodeRequest, InviteRequest, RuleRouteRequest, WeightsRequest
from ..owner import journal_of, network_of
from .app import app_change, tunnel_address
from .invites import INVITE_LINK_RE

router = APIRouter(prefix="/app/api/owner")

OWNER_TIMEOUT = 20


async def _is_owner(address: str, agent: Agent) -> str:
    answer = await agent.call(Command.APP_ME, address, timeout=OWNER_TIMEOUT)
    if answer.get("ok") is not True or answer.get("owner") is not True:
        raise HTTPException(HTTPStatus.FORBIDDEN, "только для владельца сети")
    return address


async def owner_device(address: Annotated[str, Depends(tunnel_address)], agent: Agent) -> str:
    """A device of an owner of the network; anybody else is refused."""
    return await _is_owner(address, agent)


async def owner_change(address: Annotated[str, Depends(app_change)], agent: Agent) -> str:
    """…and a change of it, which needs the app's own header too."""
    return await _is_owner(address, agent)


OwnerDevice = Annotated[str, Depends(owner_device)]
OwnerChange = Annotated[str, Depends(owner_change)]


@router.get("/network")
async def network(_device: OwnerDevice, agent: Agent, board: State) -> PanelJSON:
    """The exits, the rules in their order, the groups of people and where the day's lookups went."""
    rules, groups, exits = await asyncio.gather(
        agent.call(Command.RULES_GET, timeout=30), agent.call(Command.UGROUPS_GET, timeout=30), agent.call(Command.APP_EXITS, timeout=30),
    )
    return PanelJSON(network_of(board.snapshot(), rules, groups, exits).model_dump(mode="json"))


@router.get("/journal")
async def journal(_device: OwnerDevice, agent: Agent) -> PanelJSON:
    answer = await agent.call(Command.JOURNAL, timeout=30)
    if answer.get("ok") is not True:
        return relay(answer, HTTPStatus.BAD_GATEWAY)
    return PanelJSON(journal_of(answer).model_dump(mode="json"))


@router.post("/rules")
async def route_rules(
    request: Annotated[RuleRouteRequest, Depends(Body(RuleRouteRequest, "некорректные данные", limit=8000))], _device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """Where rules lead, all the staged changes together, checked against the version the app showed."""
    payload: dict[str, object] = {"changes": [{"rule": change.rule, **({"route": change.route.value} if change.route else {})} for change in request.changes]}
    if request.version:
        payload["base"] = request.version
    return relay(await agent.call(Command.RULE_ROUTE, packed(payload), timeout=280))


@router.post("/weights")
async def set_shares(
    request: Annotated[WeightsRequest, Depends(Body(WeightsRequest, "доля: целое число процентов от 1 до 99", limit=2000))], _device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """The shares of the exits, in percent, against the weights the app showed."""
    return relay(await agent.call(Command.WEIGHTS_SET, packed(request.for_agent()), timeout=180))


@router.post("/invite")
async def invite(
    request: Annotated[InviteRequest, Depends(Body(InviteRequest, "некорректные данные", limit=300))], _device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """A new invitation: its secret, shown once (the app makes the link with the server's name it knows)."""
    answer = await agent.call(Command.INVITE_ADD, packed(request.model_dump()), timeout=30)
    if answer.get("ok") is not True:
        return relay(answer)
    return PanelJSON({"ok": True, "id": answer.get("id"), "token": answer.get("token")})


@router.post("/invite/qr")
async def invite_code(
    request: Annotated[InviteCodeRequest, Depends(Body(InviteCodeRequest, "некорректные данные", limit=500))], _device: OwnerChange,
) -> PanelJSON:
    """The link just made as a QR code, for another phone's camera."""
    if not INVITE_LINK_RE.match(request.link):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "некорректные данные")
    return PanelJSON({"ok": True, "png": await asyncio.to_thread(qr_png, request.link)})
