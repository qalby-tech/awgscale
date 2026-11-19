"""Everything the DNS gate is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

STATE_FILE_NAME = "state.json"


class DnsgateSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DNSGATE_", env_file=".env", extra="ignore")

    proxy_ip: IPv4Address = Field(description="The entry server's public address: what a listed name resolves to (required)")
    # 172.16.17.1:53  (a pydantic-core pattern: one line, no re.VERBOSE there)
    upstream: str = Field(default="172.16.17.1:53", pattern=r"^[^:\s]+:\d{1,5}$", description="The resolver everything else is forwarded to, host:port")
    list_file: Path = Field(default=Path("/lists/domains-exit.txt"), description="The shared site list, replaced by the agent")
    tokens_file: Path = Field(default=Path("/lists/dns-tokens.json"), description="The devices' personal codes, replaced by the agent")
    data_dir: Path = Field(default=Path("/data"), description="Where the admissions and the devices' counters are kept between restarts")
    static_allow: Annotated[frozenset[str], NoDecode] = Field(default=frozenset(), description="Addresses that need no code, space separated")
    allow_hours: float = Field(default=6, gt=0, description="For how long the proxy admits an address a valid code was seen from")
    own_name: str = Field(default="", description="The server's own name: never proxied")
    bind: IPv4Address = Field(default=IPv4Address("0.0.0.0"), description="Address both listeners bind to (inside the container)")
    port: int = Field(default=8053, ge=1, le=65535, description="Port of the DoH listener")
    sni_port: int = Field(default=8445, ge=1, le=65535, description="Port of the SNI proxy")
    save_seconds: float = Field(default=30, gt=0, description="Seconds between two saves of the admissions")

    @field_validator("static_allow", mode="before")
    @classmethod
    def split_spaces(cls, value: object) -> object:
        return frozenset(value.split()) if isinstance(value, str) else value

    @field_validator("own_name")
    @classmethod
    def lowered(cls, value: str) -> str:
        return value.lower()

    @property
    def upstream_host(self) -> str:
        return self.upstream.rsplit(":", 1)[0]

    @property
    def upstream_port(self) -> int:
        return int(self.upstream.rsplit(":", 1)[1])

    @property
    def state_file(self) -> Path:
        return self.data_dir / STATE_FILE_NAME

    @property
    def allow_for(self) -> timedelta:
        return timedelta(seconds=int(self.allow_hours * 3600))


@lru_cache
def get_settings() -> DnsgateSettings:
    """The settings of this process: read once, the same object for everybody."""
    return DnsgateSettings()
