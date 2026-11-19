"""Everything the users service is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AWG_DIR = Path("/etc/amnezia/amneziawg")


class UsersSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="USERS_", env_file=".env", extra="ignore")

    iface: str = Field(default="awg0", min_length=1, max_length=15, description="The users' tunnel interface")
    conf: Path | None = Field(default=None, description="The file the tunnel boots from; by default <iface>.conf in the tunnels' directory")
    awg: Path = Field(default=Path("/usr/bin/awg"), description="The host's awg tool (it must match the kernel module)")
    dsn: str = Field(default="host=127.0.0.1 port=5432 dbname=vpn user=vpn connect_timeout=3", description="Where the database is, without the password")
    db_password_file: Path = Field(default=Path("/run/secrets/db-password"), description="File with the database password")
    token_file: Path = Field(default=Path("/run/secrets/users-token"), description="File with the bearer token callers present")
    bind: IPv4Address = Field(default=IPv4Address("127.0.0.1"), description="Address the service listens on")
    port: int = Field(default=8790, ge=1, le=65535, description="Port the service listens on")
    endpoint: str = Field(default="", description="The name devices connect to; the service does not start without it")
    description: str = Field(default="AWG Server", description="How the server is called in the AmneziaVPN app")
    dns: IPv4Address = Field(default=IPv4Address("172.16.17.1"), description="The DNS server devices are given")
    keepalive: int = Field(default=33, ge=0, le=65535, description="PersistentKeepalive of the devices, seconds")
    client_allowed: Annotated[list[str], NoDecode] = Field(default=["0.0.0.0/0", "::/0"], description="AllowedIPs of the devices, comma separated")
    max_users: int = Field(default=240, ge=1, validation_alias="USERS_MAX", description="How many users there may be")
    ip: str = Field(default="ip", min_length=1, description="The ip tool; the service listens to its `monitor link` for the tunnel coming up")
    retry_seconds: float = Field(default=30, gt=0, description="Seconds to wait before asking again when the database or the kernel did not answer")

    @field_validator("client_allowed", mode="before")
    @classmethod
    def split_commas(cls, value: object) -> object:
        return [part.strip() for part in value.split(",")] if isinstance(value, str) else value

    @property
    def conf_path(self) -> Path:
        return self.conf or AWG_DIR / f"{self.iface}.conf"


@lru_cache
def get_settings() -> UsersSettings:
    """The settings of this process: read once, the same object for everybody."""
    return UsersSettings()
