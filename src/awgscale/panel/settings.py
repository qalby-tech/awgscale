"""Everything the panel server is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PanelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PANEL_", env_file=".env", extra="ignore")

    bind: IPv4Address = Field(default=IPv4Address("127.0.0.1"), description="Address the server listens on")
    port: int = Field(default=8787, ge=1, le=65535, description="Port of the page and its API")
    agent_socket: Path = Field(default=Path("/run/vpn-panel/agent.sock"), description="Unix socket of the agent: the only thing the server asks")
    password_file: Path = Field(default=Path("/data/.password"), description="File with the admin's password; made on the first start")
    behind_tls_proxy: bool = Field(default=False, description="TLS is ended by a proxy in front (Traefik): cookies are Secure, HSTS is sent")
    tls_cert: Path | None = Field(default=None, description="Certificate, when the server ends TLS itself")
    tls_key: Path | None = Field(default=None, description="Key of that certificate")
    metrics_port: int = Field(default=0, ge=0, le=65535, description="Port Prometheus scrapes, never published; 0 is no metrics")
    prometheus_url: str = Field(default="", description="Where Prometheus is asked for the traffic history; empty is no history")
    web_dir: Path = Field(default=Path("/app/web"), description="The built page: its index and what it loads from /_next/")
    fonts_dir: Path = Field(default=Path("/app/fonts"), description="The page's typefaces")
    invite_file: Path = Field(default=Path("/app/invite.html"), description="The page a person with an invitation link gets")
    poll_seconds: float = Field(default=5, gt=0, description="Seconds between two looks at the state of the cascade")
    history_points: int = Field(default=180, ge=1, description="How many looks the speed chart keeps (15 minutes at 5 s)")
    session_hours: float = Field(default=12, gt=0, description="How long a login lasts")

    @property
    def secure(self) -> bool:
        """The page is reached over TLS, whoever ends it."""
        return bool(self.tls_cert) or self.behind_tls_proxy

    @property
    def session_ttl(self) -> timedelta:
        return timedelta(hours=self.session_hours)

    @property
    def prometheus(self) -> str:
        return self.prometheus_url.rstrip("/")


@lru_cache
def get_settings() -> PanelSettings:
    """The settings of this process: read once, the same object for everybody."""
    return PanelSettings()
