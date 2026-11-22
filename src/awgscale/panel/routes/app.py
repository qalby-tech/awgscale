"""The app on a user's own device: who am I, where can I go, keep me there.

Traefik lets only the users' tunnel reach these paths (an entrypoint of its
own, allow-listed to the tunnel), and sets X-Real-Ip from the connection
itself, so the address a request comes from says whose device it is. A page
of another site open on that device cannot send the header a change needs.
"""
from __future__ import annotations

from http import HTTPStatus
from ipaddress import IPv4Address
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from awgscale.agent.commands import Command

from ..agent import AgentAnswer
from ..deps import Agent, Body, Caller, PanelJSON, Settings, relay
from ..models import AppExitRequest, AppFilterRequest

router = APIRouter(prefix="/app/api")

APP_HEADER_VALUE = "1"


def tunnel_address(caller: Caller, settings: Settings) -> str:
    """The tunnel address of the device that asks; anybody outside the tunnel is refused."""
    try:
        address = IPv4Address(caller)
    except ValueError:
        raise HTTPException(HTTPStatus.FORBIDDEN, "только через VPN") from None
    if address not in settings.tunnel_subnet:
        raise HTTPException(HTTPStatus.FORBIDDEN, "только через VPN")
    return str(address)


def app_change(address: Annotated[str, Depends(tunnel_address)], x_awgscale: Annotated[str, Header()] = "") -> str:
    """A change needs the app's own header: a form or a script of another site cannot set it."""
    if x_awgscale != APP_HEADER_VALUE:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "bad request")
    return address


Device = Annotated[str, Depends(tunnel_address)]
ChangingDevice = Annotated[str, Depends(app_change)]


@router.get("/me")
async def me(device: Device, agent: Agent) -> PanelJSON:
    return relay(await agent.call(Command.APP_ME, device, timeout=20), HTTPStatus.NOT_FOUND)


@router.get("/exits")
async def exits(_device: Device, agent: Agent) -> AgentAnswer:
    return await agent.call(Command.APP_EXITS, timeout=20)


@router.post("/exit")
async def choose_exit(
    request: Annotated[AppExitRequest, Depends(Body(AppExitRequest, "некорректные данные"))], device: ChangingDevice, agent: Agent,
) -> PanelJSON:
    return relay(await agent.call(Command.APP_EXIT, device, str(request.exit), timeout=60))


@router.post("/filter")
async def choose_filter(
    request: Annotated[AppFilterRequest, Depends(Body(AppFilterRequest, "некорректные данные"))], device: ChangingDevice, agent: Agent,
) -> PanelJSON:
    return relay(await agent.call(Command.APP_FILTER, device, str(request.filter), timeout=60))
