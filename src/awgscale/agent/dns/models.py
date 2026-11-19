"""The smart-DNS devices, and what the page is told about them."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Device(BaseModel):
    """A device as stored, under its code."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the device")
    created: datetime = Field(description="When its code was made")


class DeviceShown(BaseModel):
    """A device as the page lists it: what is stored, and what the DNS gate has seen of it."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Name of the device")
    token: str = Field(description="The code in its DNS address")
    created: int = Field(description="When the code was made, Unix seconds")
    last: Any = Field(description="When the device last asked, as the gate noted it")
    ip: Any = Field(description="The address it last asked from")
    queries: Any = Field(description="How many requests it has made")
    admitted: bool = Field(description="Its address is let through the proxy right now")


class DevicesShown(BaseModel):
    """The answer of dns-list."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    devices: list[DeviceShown] = Field(description="The devices, by name")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class DeviceAdded(BaseModel):
    """The answer of dns-add."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    name: str = Field(description="Name of the new device")
    token: str = Field(description="Its code")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class DeviceRemoved(BaseModel):
    """The answer of dns-remove."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    name: str = Field(description="The device whose code is no more")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()
