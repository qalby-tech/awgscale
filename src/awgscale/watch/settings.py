"""Everything the watch service is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Address
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .places import DEFAULT_DIR, DEFAULT_RUN, Places


class WatchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WATCH_", env_file=".env", extra="ignore")

    bind: IPv4Address = Field(default=IPv4Address("127.0.0.1"), description="Address the service listens on")
    port: int = Field(default=8791, ge=1, le=65535, description="Port the service listens on")
    token_file: Path = Field(default=Path("/run/secrets/watch-token"), description="File with the bearer token callers present")
    cascade_dir: Path = Field(default=Path(DEFAULT_DIR), validation_alias="CASCADE_DIR", description="What the agent compiles and the deploy ships: cascade.conf, the lists, the pins")
    cascade_run: Path = Field(default=Path(DEFAULT_RUN), validation_alias="CASCADE_RUN", description="The health counters of the exits; they outlive a restart")
    ip: str = Field(default="ip", min_length=1, description="The ip tool; the service listens to its `monitor link` for a tunnel coming up")
    probe_seconds: float = Field(default=15, gt=0, description="Seconds between two probes of the exits")
    save_seconds: float = Field(default=300, gt=0, description="Seconds between two saves of the addresses learned for the listed domains")
    refresh_check_seconds: float = Field(default=300, gt=0, description="Seconds between two looks at whether an address list is due for a refresh")
    refresh_days: float = Field(default=7, gt=0, description="An address list older than this many days is downloaded again")
    refresh_retry_hours: float = Field(default=6, gt=0, description="Hours before a source that did not answer is asked again")
    apply_retry_seconds: float = Field(default=15, gt=0, description="Seconds between two attempts to put the rules in at the start")
    apply_attempts: int = Field(default=40, ge=1, description="How many times the rules are tried at the start before the service goes on without them")
    link_settle_seconds: float = Field(default=1, ge=0, description="Seconds given to a tunnel that has just come up before it is probed")

    @property
    def places(self) -> Places:
        return Places(dir=self.cascade_dir, run=self.cascade_run)


@lru_cache
def get_settings() -> WatchSettings:
    """The settings of this process: read once, the same object for everybody."""
    return WatchSettings()
