"""Everything the bot is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from datetime import timedelta, timezone
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

STATS_FILE_NAME = "stats.json"


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOT_", env_file=".env", extra="ignore", populate_by_name=True)

    token_file: Path = Field(default=Path("/run/secrets/bot-token"), description="File with the bot's Telegram token")
    admin_id: int = Field(repr=False, description="Telegram id of the admin: the only one the bot answers (required)")
    domain: str = Field(default="", description="The name of the entry server: where the invitation pages live")
    data_dir: Path = Field(default=Path("/data"), description="Where the day's counters are kept between restarts")
    api_url: str = Field(default="https://api.telegram.org", description="The Telegram Bot API")
    agent_socket: Path = Field(default=Path("/run/vpn-panel/agent.sock"), validation_alias="PANEL_AGENT_SOCKET", description="The agent's unix socket")
    report_hour: int = Field(default=9, ge=0, le=23, validation_alias="REPORT_HOUR", description="The hour of the daily summary, local time")
    report_utc_offset: int = Field(default=3, ge=-12, le=14, validation_alias="REPORT_UTC_OFFSET", description="Local time as hours from UTC (3 is Moscow)")
    watch_seconds: float = Field(default=30, gt=0, description="Seconds between two looks at the cascade for the alerts")
    sample_seconds: float = Field(default=60, gt=0, description="Seconds between two samples of the counters")
    poll_seconds: int = Field(default=10, ge=0, description="For how long Telegram may hold a request for updates open")
    retry_seconds: float = Field(default=3, gt=0, description="Seconds to wait before asking Telegram again after a failure")

    @property
    def stats_file(self) -> Path:
        return self.data_dir / STATS_FILE_NAME

    @property
    def local_zone(self) -> timezone:
        return timezone(timedelta(hours=self.report_utc_offset))

    def token(self) -> SecretStr:
        """The token as the file holds it now; never logged or shown in repr."""
        return SecretStr(self.token_file.read_text().strip())


@lru_cache
def get_settings() -> BotSettings:
    """The settings of this process: read once, the same object for everybody."""
    return BotSettings()  # type: ignore[call-arg]
