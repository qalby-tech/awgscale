"""Everything the resolver's keeper is told from outside (.env, or the container's environment)."""
from __future__ import annotations

import json
from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ADGUARD = "/opt/adguardhome/AdGuardHome"


class ResolverSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESOLVER_", env_file=".env", extra="ignore", populate_by_name=True)

    bind: IPv4Address = Field(default=IPv4Address("127.0.0.1"), description="Address the keeper listens on")
    port: int = Field(default=8792, ge=1, le=65535, description="Port the keeper listens on")
    token_file: Path = Field(default=Path("/run/secrets/resolver-token"), validation_alias="RESOLVER_TOKEN", description="File with the bearer token the agent presents")
    home: Path = Field(default=Path("/opt/AdGuardHome"), description="Where the resolver keeps its config and data")
    command: Annotated[list[str] | None, NoDecode] = Field(default=None, min_length=1, description="The resolver's command line as a JSON list; by default AdGuard Home on its config in `home`")
    # 172.16.17.1:53  (a pydantic-core pattern: one line, no re.VERBOSE there)
    dns: str = Field(default="172.16.17.1:53", pattern=r"^[^:\s]+:\d{1,5}$", description="Where the resolver must listen once started, host:port")
    hold_seconds: float = Field(default=150, gt=0, validation_alias="RESOLVER_HOLD", description="A resolver stopped on request starts by itself after this long; more than the agent's longest step between stop and start")
    stop_seconds: float = Field(default=30, gt=0, validation_alias="RESOLVER_STOP_WAIT", description="How long the resolver is given to exit before it is killed")
    listen_seconds: float = Field(default=20, gt=0, validation_alias="RESOLVER_LISTEN_WAIT", description="How long a start waits for the resolver to listen")
    retry_seconds: float = Field(default=3, gt=0, validation_alias="RESOLVER_RETRY", description="Seconds before a resolver that is not running is started again")

    @field_validator("command", mode="before")
    @classmethod
    def json_list_or_nothing(cls, value: object) -> object:
        """Written as a JSON list; left empty it means the default."""
        if isinstance(value, str):
            return json.loads(value) if value.strip() else None
        return value

    @property
    def child_command(self) -> list[str]:
        if self.command:
            return self.command
        return [ADGUARD, "-c", str(self.home / "AdGuardHome.yaml"), "-w", str(self.home), "--no-check-update"]

    @property
    def dns_host(self) -> str:
        return self.dns.rpartition(":")[0]

    @property
    def dns_port(self) -> int:
        return int(self.dns.rpartition(":")[2])


@lru_cache
def get_settings() -> ResolverSettings:
    """The settings of this process: read once, the same object for everybody."""
    return ResolverSettings()
