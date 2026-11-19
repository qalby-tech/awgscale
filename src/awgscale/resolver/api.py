"""The routes of the resolver's keeper."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .factory import get_keeper
from .models import Done, Health
from .service import Keeper

Service = Annotated[Keeper, Depends(get_keeper)]

router = APIRouter(prefix="/v1")


@router.get("/health")
async def health(keeper: Service) -> Health:
    return await keeper.health()


@router.post("/stop")
async def stop(keeper: Service) -> Done:
    await keeper.stop()
    return Done()


@router.post("/start")
async def start(keeper: Service) -> Done:
    await keeper.start()
    return Done()
