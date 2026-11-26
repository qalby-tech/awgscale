"""The owner's part of the app: the network read and changed from a device of an owner.

Whose device asks is said by its tunnel address (routes/app.py); whether that user owns the network is asked of the
agent on every request (app-me), so a user who stopped being an owner loses these paths at once. An agent that cannot
be asked refuses them too. What is shown is a summary: no user's key, endpoint or connection code."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from awgscale.agent.commands import Command, Switch
from awgscale.agent.state.clients import ClientSort
from awgscale.service.qr import qr_png

from ..arguments import packed
from ..deps import Agent, Body, PanelJSON, Settings, State, relay
from ..models import (
    ClientsParams, InviteCodeRequest, InviteRequest, ModeRequest, NamesRequest, OwnerWeightsRequest, PeopleParams, RuleRouteRequest, SuspendRequest,
)
from ..owner import PEOPLE_SHOWN, journal_of, network_of, owners_of, people_of
from .app import app_change, tunnel_address
from .exits import refuse_no_exit_left
from .invites import INVITE_LINK_RE

router = APIRouter(prefix="/app/api/owner")

OWNER_TIMEOUT = 20


@dataclass(frozen=True)
class Asking:
    """The owner's device that asks: its tunnel address and its user's name (the journal names it)."""

    address: str
    name: str


async def _owner(address: str, agent: Agent) -> Asking:
    answer = await agent.call(Command.APP_ME, address, timeout=OWNER_TIMEOUT)
    name = answer.get("name")
    if answer.get("ok") is not True or answer.get("owner") is not True or not isinstance(name, str) or not name:
        raise HTTPException(HTTPStatus.FORBIDDEN, "только для владельца сети")
    return Asking(address=address, name=name)


async def owner_device(address: Annotated[str, Depends(tunnel_address)], agent: Agent) -> Asking:
    """A device of an owner of the network; anybody else is refused."""
    return await _owner(address, agent)


async def owner_change(address: Annotated[str, Depends(app_change)], agent: Agent) -> Asking:
    """…and a change of it, which needs the app's own header too."""
    return await _owner(address, agent)


OwnerDevice = Annotated[Asking, Depends(owner_device)]
OwnerChange = Annotated[Asking, Depends(owner_change)]


@router.get("/network")
async def network(device: OwnerDevice, agent: Agent, board: State, settings: Settings) -> PanelJSON:
    """The exits, the rules in their order, the groups of people, the owners and where the day's lookups went."""
    rules, groups, exits, owners = await asyncio.gather(
        agent.call(Command.RULES_GET, timeout=30), agent.call(Command.UGROUPS_GET, timeout=30), agent.call(Command.APP_EXITS, timeout=30),
        agent.call(Command.APP_OWNERS, timeout=30),
    )
    summary = network_of(board.snapshot(), rules, groups, exits, owners, domain=settings.domain, me=device.name)
    return PanelJSON(summary.model_dump(mode="json"))


@router.get("/journal")
async def journal(_device: OwnerDevice, agent: Agent) -> PanelJSON:
    answer = await agent.call(Command.JOURNAL, timeout=30)
    if answer.get("ok") is not True:
        return relay(answer, HTTPStatus.BAD_GATEWAY)
    return PanelJSON(journal_of(answer).model_dump(mode="json"))


@router.post("/rules")
async def route_rules(
    request: Annotated[RuleRouteRequest, Depends(Body(RuleRouteRequest, "некорректные данные", limit=8000))], device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """Where rules lead, whom they are for and their order, all the staged changes together, checked against the
    version the app showed."""
    payload: dict[str, object] = {
        "changes": [
            {"rule": change.rule, **({"route": change.route.value} if change.route else {}), **({"groups": change.groups} if change.groups is not None else {})}
            for change in request.changes
        ]
    }
    if request.order is not None:
        payload["order"] = request.order
    if request.version:
        payload["base"] = request.version
    return relay(await agent.call(Command.RULE_ROUTE, packed(payload), timeout=280, device=device.name))


@router.post("/mode")
async def set_mode(request: Annotated[ModeRequest, Depends(Body(ModeRequest, "некорректный режим"))], device: OwnerChange, agent: Agent) -> PanelJSON:
    """What goes abroad: the listed sites, or everything foreign."""
    return relay(await agent.call(Command.MODE_SET, request.mode.value, timeout=240, device=device.name))


@router.post("/weights")
async def set_shares(
    request: Annotated[OwnerWeightsRequest, Depends(Body(OwnerWeightsRequest, "доля: целое число процентов от 0 (выключен) до 100", limit=2000))],
    device: OwnerChange,
    agent: Agent,
    board: State,
) -> PanelJSON:
    """The shares of the exits, in percent, against the weights the app showed; 0 switches an exit off. Switching
    off never leaves the network with no exit that is on and works (as the last look saw them)."""
    refuse_no_exit_left(board, request.weights)
    return relay(await agent.call(Command.WEIGHTS_SET, packed(request.for_agent()), timeout=180, device=device.name))


# ── people ───────────────────────────────────────────────────────────────────

async def _owners(agent: Agent) -> list[str]:
    """The owners now; an agent that cannot say refuses the change (nothing is done blind)."""
    answer = await agent.call(Command.APP_OWNERS, timeout=OWNER_TIMEOUT)
    if answer.get("ok") is not True:
        raise HTTPException(HTTPStatus.BAD_GATEWAY, "агент не сказал, кто владельцы сети — ничего не изменено")
    return owners_of(answer)


def _spared(names: list[str], device: Asking, owners: list[str]) -> None:
    """Neither the device that asks nor every owner: the network is never left without anybody to run it."""
    if device.name in names:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "это устройство, с которого вы управляете сетью: его здесь не остановить и не удалить")
    if owners and set(owners) <= set(names):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "так в сети не останется ни одного владельца")


@router.get("/people")
async def people(params: Annotated[PeopleParams, Query()], device: OwnerDevice, agent: Agent, board: State) -> PanelJSON:
    """One page of people, of one group or of all, with whether they are online or paused."""
    query = ClientsParams(group=params.group, q=params.q, offset=params.offset, limit=PEOPLE_SHOWN, sort=ClientSort.NAME)
    page, owners = await asyncio.gather(board.clients(packed(query.for_agent())), agent.call(Command.APP_OWNERS, timeout=OWNER_TIMEOUT))
    if not page.ok:
        return PanelJSON({"ok": False, "error": page.error or "агент не ответил"}, HTTPStatus.BAD_GATEWAY)
    return PanelJSON(people_of(page, owners_of(owners), device.name).model_dump(mode="json"))


@router.post("/people/pause")
async def pause_people(
    request: Annotated[SuspendRequest, Depends(Body(SuspendRequest, "некорректные данные", limit=4000))], device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """Pause people or let them back; the device that asks and the last owner are never paused."""
    if request.on:
        _spared(request.names, device, await _owners(agent))
    return relay(await agent.call(Command.SUSPEND, Switch.ON if request.on else Switch.OFF, *request.names, timeout=60, device=device.name))


@router.post("/people/remove")
async def remove_people(
    request: Annotated[NamesRequest, Depends(Body(NamesRequest, "некорректные данные", limit=4000))], device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """Remove people for good; the device that asks and the last owner are never removed."""
    _spared(request.names, device, await _owners(agent))
    return relay(await agent.call(Command.REMOVE, *request.names, timeout=170, device=device.name), HTTPStatus.BAD_GATEWAY)


@router.post("/invite")
async def invite(
    request: Annotated[InviteRequest, Depends(Body(InviteRequest, "некорректные данные", limit=300))], device: OwnerChange, agent: Agent,
) -> PanelJSON:
    """A new invitation: its secret, shown once (the app makes the link with the server's public name)."""
    answer = await agent.call(Command.INVITE_ADD, packed(request.model_dump()), timeout=30, device=device.name)
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
