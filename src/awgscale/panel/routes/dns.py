"""Smart DNS: the devices that use the DNS without the VPN."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from awgscale.agent.commands import Command

from ..agent import AgentAnswer
from ..arguments import checked_name
from ..deps import LOGGED_IN, MAY_CHANGE, Agent, Body, PanelJSON, relay
from ..models import DeviceRequest

router = APIRouter(prefix="/api")


@router.get("/dns", dependencies=LOGGED_IN)
async def devices(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.DNS_LIST, timeout=30)


@router.post("/dns", dependencies=MAY_CHANGE)
async def add_device(request: Annotated[DeviceRequest, Depends(Body(DeviceRequest, "имя: латиница, цифры, _ и -, до 32 символов"))], agent: Agent) -> PanelJSON:
    return relay(await agent.call(Command.DNS_ADD, request.name, timeout=30))


@router.delete("/dns/{name:path}", dependencies=MAY_CHANGE)
async def remove_device(name: str, agent: Agent) -> PanelJSON:
    return relay(await agent.call(Command.DNS_REMOVE, checked_name(name), timeout=30))
