"""Everything the exits service is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_REMOTE = Path(__file__).parent / "remote"


class ExitsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXITS_", env_file=".env", extra="ignore")

    dir: Path = Field(default=Path("/etc/amnezia/amneziawg"), description="Where the entry's link files live")
    secret: Path = Field(default=Path("/secret"), description="The SSH key and the known hosts (mounted in no other container)")
    token_file: Path = Field(default=Path("/run/secrets/exits-token"), description="File with the bearer token the agent presents")
    remote: Path = Field(default=PACKAGE_REMOTE, description="The scripts run on an exit server")
    ppa_key: Path = Field(default=Path("/ppa/amnezia-ppa.gpg"), description="The Amnezia PPA's key, sent to a new server")
    entry_addr: str = Field(default="", description="The address the exits see the entry come from (the key is accepted only from it)")
    client_conf: Path | None = Field(default=None, description="The users' tunnel file (its I1 line is reused by the links); by default awg0.conf in the links' directory")
    agent_socket: Path = Field(default=Path("/run/vpn-panel/agent.sock"), description="Where the agent listens")
    ssh: str = Field(default="ssh", min_length=1, description="The ssh client")
    awg: str = Field(default="/usr/bin/awg", min_length=1, description="The host's awg tool (it must match the kernel module)")
    dsn: str = Field(default="host=127.0.0.1 port=5432 dbname=vpn user=vpn connect_timeout=3", description="Where the database is, without the password")
    db_password_file: Path = Field(default=Path("/run/secrets/db-password"), description="File with the database password")
    state_file: Path | None = Field(default=None, description="Keep the state in this file instead of the database (the tests)")
    bind: IPv4Address = Field(default=IPv4Address("127.0.0.1"), description="Address the service listens on")
    port: int = Field(default=8793, ge=1, le=65535, description="Port the service listens on")
    wait: float = Field(default=1, ge=0, description="Seconds between two looks while a step waits for something; 0 is do not wait (the tests)")
    stats_port: int = Field(default=9101, ge=1, le=65535, description="Where an exit's stats service listens, inside its tunnel")

    @property
    def key_file(self) -> Path:
        return self.secret / "id_ed25519"

    @property
    def known_hosts(self) -> Path:
        return self.secret / "known_hosts"

    @property
    def users_tunnel_file(self) -> Path:
        return self.client_conf or self.dir / "awg0.conf"


@lru_cache
def get_settings() -> ExitsSettings:
    """The settings of this process: read once, the same object for everybody."""
    return ExitsSettings()
