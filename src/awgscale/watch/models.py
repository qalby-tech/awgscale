"""The answers of the watch service, as objects."""
from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from .cascade import CascadeStatus, ExitStatus
from .conf import RouteMode
from .health import ExitState


class Health(BaseModel):
    ok: bool = Field(default=True, description="The service is running")
    started: datetime = Field(description="When this process started")
    applied: datetime | None = Field(description="When the rules were last put in; None while they never were")
    last_tick: datetime | None = Field(description="When the exits were last probed; None before the first pass")


class Work(BaseModel):
    """What the service did by itself since it started."""

    started: datetime = Field(description="When this process started")
    applied: datetime | None = Field(description="When the rules were last put in")
    last_tick: datetime | None = Field(description="When the last pass ended")
    tick_seconds: float = Field(description="How long the last pass took")
    ticks: int = Field(ge=0, description="Passes made")
    tick_failed: int = Field(ge=0, description="Passes that failed")


class ExitView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    index: int = Field(description="The exit's number in cascade.conf")
    name: str = Field(description="The exit's name")
    iface: str = Field(description="The tunnel interface to it")
    state: ExitState = Field(description="up, down, or unknown while there is no verdict yet")
    handshake: datetime | None = Field(description="The tunnel's latest handshake; None when there was none")
    rx: int = Field(ge=0, description="Bytes received through the tunnel")
    tx: int = Field(ge=0, description="Bytes sent through the tunnel")
    clients: list[IPv4Address] = Field(description="Tunnel addresses of the users pinned to this exit")
    probe_ms: float | None = Field(description="How long the last probe took; None when it was not answered or not made")
    changed: datetime | None = Field(description="When the state last changed, since this process started")

    @classmethod
    def of(cls, status: ExitStatus) -> ExitView:
        return cls.model_validate(status)


class Status(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    time: datetime = Field(description="When this answer was made")
    exits: list[ExitView] = Field(description="The exits, in the order of cascade.conf")
    udp_via_exit: bool = Field(description="UDP to foreign addresses (calls) goes through an exit")
    route_mode: RouteMode = Field(description="What goes abroad: the list, or everything foreign")
    force_exit_nets: int = Field(ge=0, description="Listed networks loaded")
    ru_networks: int = Field(ge=0, description="Russian networks loaded")
    force_exit_ips: int = Field(ge=0, description="Addresses learned for the listed domains")
    force_direct_ips: int = Field(ge=0, description="Addresses learned for the domains forced direct")
    single_server: bool = Field(default=False, description="A single server: no exits, nothing routed by country; said only when it is on")
    watch: Work = Field(description="The service's own work")

    @model_serializer(mode="wrap")
    def without_cascade_default(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """`single_server` only when it is on: a cascade's answer stays what it was."""
        shown: dict[str, Any] = handler(self)
        if not self.single_server:
            shown.pop("single_server", None)
        return shown

    @classmethod
    def of(cls, status: CascadeStatus, work: Work) -> Status:
        return cls(
            time=status.time,
            exits=[ExitView.of(e) for e in status.exits],
            udp_via_exit=status.udp_via_exit,
            route_mode=status.route_mode,
            force_exit_nets=status.force_exit_nets,
            ru_networks=status.ru_networks,
            force_exit_ips=status.force_exit_ips,
            force_direct_ips=status.force_direct_ips,
            single_server=status.single_server,
            watch=work,
        )


class Applied(BaseModel):
    ok: bool = Field(default=True, description="The rules are in")
    mode: RouteMode = Field(description="The mode they were installed for")


class Pinned(BaseModel):
    ok: bool = Field(description="False when another pass was running and this one was skipped")


class SpacesLoaded(BaseModel):
    ok: bool = Field(default=True, description="The spaces are loaded as compiled")
    spaces: int = Field(ge=0, description="How many are active")


class DomainsReset(BaseModel):
    ok: bool = Field(default=True, description="The listed networks are loaded again, learned addresses forgotten")
    nets: int = Field(ge=0, description="How many networks are listed")


class RuRefreshed(BaseModel):
    ok: bool = Field(default=True, description="The Russian address list is loaded")
    ru_networks: int = Field(ge=0, description="How many networks it has")
    refreshed: bool = Field(description="False when no source answered and the local copy was kept")


class NetsRefreshed(BaseModel):
    ok: bool = Field(default=True, description="The automatic networks are loaded")
    nets: int = Field(ge=0, description="How many were downloaded")
    refreshed: bool = Field(description="False when the source did not answer and the previous list was kept")
