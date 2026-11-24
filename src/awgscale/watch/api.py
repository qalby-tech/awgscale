"""The routes of the watch service."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .factory import get_service
from .models import Applied, DomainsReset, Health, NetsRefreshed, Pinned, RuRefreshed, ScopedLoaded, SpacesLoaded, Status
from .service import WatchService

Service = Annotated[WatchService, Depends(get_service)]

router = APIRouter(prefix="/v1")


@router.get("/health")
async def health(service: Service) -> Health:
    return service.health()


@router.get("/status")
async def status(service: Service) -> Status:
    return await service.status()


@router.post("/apply")
async def apply(service: Service) -> Applied:
    return await service.apply()


@router.post("/pin")
async def pin(service: Service) -> Pinned:
    return await service.pin()


@router.post("/spaces")
async def spaces(service: Service) -> SpacesLoaded:
    return await service.spaces()


@router.post("/scoped")
async def scoped(service: Service) -> ScopedLoaded:
    return await service.scoped()


@router.post("/domains")
async def domains(service: Service) -> DomainsReset:
    return await service.domains()


@router.post("/update-ru")
async def update_ru(service: Service) -> RuRefreshed:
    return await service.update_ru()


@router.post("/update-nets")
async def update_nets(service: Service) -> NetsRefreshed:
    return await service.update_nets()
