"""What the tunnel service is told from outside (the container's environment, or .env)."""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Role(StrEnum):
    """Which interfaces a process keeps."""

    CLIENTS = "clients"          # the users' tunnel: one interface, with its firewall rules and the gate
    EXIT_LINKS = "exit-links"    # every link to an exit that has a file: no rules, no routes, no gate


class TunnelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TUNNEL_", env_file=".env", extra="ignore")

    iface: str = Field(default="awg0", min_length=1, max_length=15, description="The users' tunnel interface (role: clients)")
    glob: str = Field(default="", description="Set, e.g. 'awgk*': keep every exit link whose file matches (role: exit links)")
    dir: Path = Field(default=Path("/etc/amnezia/amneziawg"), description="Where the interface files live")
    conf: Path | None = Field(default=None, description="The file of the users' tunnel; by default <dir>/<iface>.conf")
    awg: str = Field(default="/usr/bin/awg", min_length=1, description="The host's own awg tool (it must match the kernel module)")
    ip: str = Field(default="ip", min_length=1, description="The ip tool; the service listens to its `monitor link` for links that appear, change or go")
    link_type: str = Field(default="amneziawg", min_length=1, description="The kernel link type (the tests have no such module: dummy)")
    gate_chain: str = Field(default="CASCADE", min_length=1, description="The mangle chain whose jump for the interface means: the rules are in")
    pass_seconds: float = Field(
        default=10,
        gt=0,
        validation_alias="TUNNEL_PASS",
        description="Seconds between two looks at what the kernel does not announce: firewall rules, the gate, new files",
    )
    retry_seconds: float = Field(default=10, gt=0, description="How soon to listen to the kernel's link events again after they stopped")

    @property
    def role(self) -> Role:
        return Role.EXIT_LINKS if self.glob else Role.CLIENTS

    def conf_of(self, iface: str) -> Path:
        if self.role is Role.CLIENTS and self.conf is not None:
            return self.conf
        return self.dir / f"{iface}.conf"


@lru_cache
def get_settings() -> TunnelSettings:
    """The settings of this process: read once, the same object for everybody."""
    return TunnelSettings()
