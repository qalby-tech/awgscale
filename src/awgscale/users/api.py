"""The routes of the users service."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .factory import get_service
from .models import (
    AddRequest, CreatedAnswer, DevicePayload, ExpiryAnswer, ExpiryRequest, Health, RemovalsAnswer, RemoveRequest, Status, UserName, UsersAnswer,
)
from .service import UsersService

Service = Annotated[UsersService, Depends(get_service)]

router = APIRouter(prefix="/v1")


@router.get("/health")
async def health(service: Service) -> Health:
    return await service.health()


@router.get("/users")
async def list_users(service: Service) -> UsersAnswer:
    return UsersAnswer(users=await service.list())


@router.get("/users/{name}/config")
async def device_config(name: UserName, service: Service) -> DevicePayload:
    return await service.config(name)


@router.post("/users")
async def add_user(request: AddRequest, service: Service) -> CreatedAnswer:
    return CreatedAnswer(results=[await service.add(request)])


@router.delete("/users")
async def remove_users(request: RemoveRequest, service: Service) -> RemovalsAnswer:
    removals = await service.remove(request.names)
    return RemovalsAnswer(ok=all(removal.status is Status.REMOVED for removal in removals), results=removals)


@router.post("/users/expiry")
async def set_expiry(request: ExpiryRequest, service: Service) -> ExpiryAnswer:
    return ExpiryAnswer(names=request.names, expires_at=await service.set_expiry(request))
