"""What the page shows without asking the agent anew: the state of the cascade, the traffic history, the change journal."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from awgscale.agent.commands import Command

from ..agent import AgentAnswer
from ..deps import LOGGED_IN, Agent, Files, History, Now, State

router = APIRouter(prefix="/api", dependencies=LOGGED_IN)


@router.get("/state")
async def state(board: State, files: Files, now: Now) -> dict[str, Any]:
    return board.snapshot().model_copy(update={"server_time": now, "ui": files.build_id()}).wire()


@router.get("/traffic")
async def traffic(history: History) -> AgentAnswer:
    """Bytes per user by day and by hour; the page asks about once a minute."""
    return await history.traffic()


@router.get("/journal")
async def journal(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.JOURNAL, timeout=30)
