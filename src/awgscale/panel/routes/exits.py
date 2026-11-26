"""The exits as servers: a rented server becomes an exit, an exit is renamed, updated, removed, told where it is; their shares."""
from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from awgscale.agent.commands import Command
from awgscale.watch.health import ExitState

from ..agent import AgentAnswer
from ..arguments import packed
from ..deps import LOGGED_IN, MAY_CHANGE, Agent, Body, PanelJSON, State, relay
from ..models import AddExitRequest, ExitCountryRequest, RemoveExitRequest, RenameExitRequest, UpdateExitRequest, WeightsRequest
from ..state import StateBoard

router = APIRouter(prefix="/api/exits")


def refuse_no_exit_left(board: StateBoard, weights: Mapping[str, int]) -> None:
    """Switching exits off (a share of 0) never leaves the network with no exit that is on and works, as the last look
    saw them; shares that switch nothing off are left to the agent."""
    if not any(share == 0 for share in weights.values()):
        return
    looks = board.snapshot().exits or []
    state_of = {str(look.index): look.state for look in looks if look.index is not None}
    stored = {str(look.index): _whole(look.weight) for look in looks if look.index is not None}
    shares = {**stored, **weights}
    if not any(share > 0 and state_of.get(index) == ExitState.UP for index, share in shares.items()):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "так не останется ни одного работающего включённого выхода")


def _whole(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def relay_change(answer: AgentAnswer, asks_again: str) -> PanelJSON:
    """An answer about a change of the exits: 409 when the agent wants the page to come back (to confirm, or with fresh shares)."""
    if answer.get("ok"):
        return relay(answer)
    return relay(answer, HTTPStatus.CONFLICT if answer.get(asks_again) else HTTPStatus.BAD_REQUEST)


@router.get("", dependencies=LOGGED_IN)
async def exits(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.EXITS_GET, timeout=30)


@router.post("/add", dependencies=MAY_CHANGE)
async def add_exit(
    request: Annotated[AddExitRequest, Depends(Body(AddExitRequest, "адрес сервера (IPv4), имя латиницей и, если нужен, пароль root", limit=2000))],
    agent: Agent,
) -> PanelJSON:
    # the password is handed to the agent beside the command and is never logged
    answer = await agent.call(Command.EXIT_ADD, packed(request.for_agent()), timeout=90, secret=request.password or None)
    return relay_change(answer, "confirm")


@router.post("/rename", dependencies=MAY_CHANGE)
async def rename_exit(
    request: Annotated[RenameExitRequest, Depends(Body(RenameExitRequest, "имя выхода: латиница, цифры, точка, дефис, до 32 знаков", limit=2000))],
    agent: Agent,
) -> PanelJSON:
    return relay_change(await agent.call(Command.EXIT_RENAME, str(request.index), request.name, timeout=180), "confirm")


@router.post("/country", dependencies=MAY_CHANGE)
async def set_exit_country(
    request: Annotated[ExitCountryRequest, Depends(Body(ExitCountryRequest, "страна выхода: код ISO из двух строчных латинских букв или auto", limit=2000))],
    agent: Agent,
) -> PanelJSON:
    """Where an exit is, as the admin says; "auto" finds it again by its address."""
    return relay_change(await agent.call(Command.EXIT_COUNTRY, str(request.index), request.country, timeout=60), "confirm")


@router.post("/remove", dependencies=MAY_CHANGE)
async def remove_exit(request: Annotated[RemoveExitRequest, Depends(Body(RemoveExitRequest, "некорректные данные", limit=2000))], agent: Agent) -> PanelJSON:
    force = ["force"] if request.force is True else []
    return relay_change(await agent.call(Command.EXIT_REMOVE, str(request.index), *force, timeout=240), "confirm")


@router.post("/update", dependencies=MAY_CHANGE)
async def update_exit(request: Annotated[UpdateExitRequest, Depends(Body(UpdateExitRequest, "некорректные данные", limit=2000))], agent: Agent) -> PanelJSON:
    reboot = ["reboot"] if request.reboot is True else []
    return relay_change(await agent.call(Command.EXIT_UPDATE, str(request.index), *reboot, timeout=90), "confirm")


@router.post("/weights", dependencies=MAY_CHANGE)
async def set_shares(
    request: Annotated[WeightsRequest, Depends(Body(WeightsRequest, "доля: целое число процентов от 0 (выключен) до 100", limit=2000))],
    agent: Agent,
    board: State,
) -> PanelJSON:
    """The shares of the exits, in percent; 0 switches an exit off."""
    refuse_no_exit_left(board, request.weights)
    return relay_change(await agent.call(Command.WEIGHTS_SET, packed(request.for_agent()), timeout=180), "conflict")
