"""The routes of the exits service."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .factory import get_service
from .models import AddRequest, Adopted, AdoptRequest, NodesAnswer, Removed, RemoveRequest, Started, UpdateRequest
from .service import ExitsService

Service = Annotated[ExitsService, Depends(get_service)]

router = APIRouter(prefix="/v1")


@router.get("/nodes")
async def nodes(service: Service) -> NodesAnswer:
    return await service.nodes()


@router.post("/add")
async def add(request: AddRequest, service: Service) -> Started:
    return await service.add(request)


@router.post("/update")
async def update(request: UpdateRequest, service: Service) -> Started:
    return await service.update(request)


@router.post("/remove")
async def remove(request: RemoveRequest, service: Service) -> Removed:
    return await service.remove(request)


@router.post("/adopt")
async def adopt(request: AdoptRequest, service: Service) -> Adopted:
    return await service.adopt(request)
