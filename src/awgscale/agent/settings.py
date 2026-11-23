"""Everything the agent is told from outside (.env, or the container's environment)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .adguard.models import GROUP_LISTS_FILE, HAGEZI_URL, THREATS_LIST_URL


class AgentSettings(BaseSettings):
    # the names the container has always been given: no common prefix
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    socket_path: Path = Field(default=Path("/run/vpn-panel/agent.sock"), validation_alias="VPN_AGENT_SOCKET", description="The unix socket the panel and the bot speak to")
    store_dsn: str = Field(default="host=127.0.0.1 port=5432 dbname=vpn user=vpn connect_timeout=3", validation_alias="VPN_STORE_DSN", description="Where the settings' database is, without the password")
    store_password_file: Path = Field(default=Path("/opt/vpn-stack/db-secret/password"), validation_alias="VPN_STORE_PASSWORD_FILE", description="File with the database password")
    cascade_dir: Path = Field(default=Path("/root/awg/cascade"), validation_alias="VPN_CASCADE_DIR", description="Where the files compiled for the routing live, and the agent's lock files")
    run_dir: Path = Field(default=Path("/run/cascade"), validation_alias="VPN_RUN_DIR", description="Where the watch service keeps what it knows now: an exit's state is read from here")
    client_iface: str = Field(default="awg0", validation_alias="VPN_CLIENT_IF", description="The users' tunnel interface")
    users_cache_file: Path = Field(default=Path("/run/cascade/panel-clients.json"), validation_alias="VPN_USERS_CACHE_FILE", description="The users' list as last asked, kept for a minute")
    tunnel_file: Path = Field(default=Path("/etc/amnezia/amneziawg/awg0.conf"), validation_alias="VPN_TUNNEL_FILE", description="The users' tunnel file: who is known when the users service cannot be asked")
    # sites and routing
    lists_dir: Path = Field(default=Path("/opt/vpn-stack/lists"), validation_alias="VPN_LISTS_DIR", description="Where the list of what goes abroad is put for the DNS-without-VPN container")
    resolver_yaml: Path = Field(default=Path("/opt/AdGuardHome/AdGuardHome.yaml"), validation_alias="VPN_RESOLVER_YAML", description="The resolver's own settings: it is told there where its file of sets is")
    users_url: HttpUrl = Field(default=HttpUrl("http://127.0.0.1:8790"), validation_alias="VPN_USERS_URL", description="The users service")
    users_token_file: Path = Field(default=Path("/opt/vpn-stack/users-secret/token"), validation_alias="VPN_USERS_TOKEN_FILE", description="File with the users service's token")
    watch_url: HttpUrl = Field(default=HttpUrl("http://127.0.0.1:8791"), validation_alias="VPN_WATCH_URL", description="The watch service")
    watch_token_file: Path = Field(default=Path("/opt/vpn-stack/watch-secret/token"), validation_alias="VPN_WATCH_TOKEN_FILE", description="File with the watch service's token")
    resolver_url: HttpUrl = Field(default=HttpUrl("http://127.0.0.1:8792"), validation_alias="VPN_RESOLVER_URL", description="The resolver's keeper")
    resolver_token_file: Path = Field(default=Path("/opt/vpn-stack/resolver-secret/token"), validation_alias="VPN_RESOLVER_TOKEN_FILE", description="File with the keeper's token")
    exits_url: HttpUrl = Field(default=HttpUrl("http://127.0.0.1:8793"), validation_alias="VPN_EXITS_URL", description="The exits service")
    exits_token_file: Path = Field(default=Path("/opt/vpn-stack/exits-secret/token"), validation_alias="VPN_EXITS_TOKEN_FILE", description="File with the exits service's token")
    loki_url: HttpUrl = Field(default=HttpUrl("http://127.0.0.1:3100"), validation_alias="VPN_LOKI_URL", description="Loki: where the journal is read back from")
    journal_socket: Path = Field(default=Path("/run/systemd/journal/socket"), validation_alias="VPN_JOURNAL_SOCKET", description="journald's native socket: where the journal is written")
    journal_id: str = Field(default="vpn-journal", validation_alias="VPN_JOURNAL_ID", description="The identifier the journal's lines carry; Alloy picks them by it")
    journal_days: int = Field(default=400, ge=1, validation_alias="VPN_JOURNAL_DAYS", description="How far back the journal is read (Loki keeps as much)")
    stop_wait_seconds: float = Field(default=170, gt=0, validation_alias="VPN_AGENT_STOP_WAIT", description="How long a running command is waited for on the way out (compose: stop_grace_period)")
    nightly_hour: int = Field(default=2, ge=0, le=23, validation_alias="VPN_AGENT_NIGHTLY_HOUR", description="UTC hour of the nightly refresh of the lists by link")
    nightly_minute: int = Field(default=20, ge=0, le=59, validation_alias="VPN_AGENT_NIGHTLY_MINUTE", description="…and its minute")
    nightly_jitter_seconds: int = Field(default=900, ge=0, validation_alias="VPN_AGENT_NIGHTLY_JITTER", description="The refresh starts up to this many seconds later")
    sample_seconds: float = Field(default=5, gt=0, validation_alias="VPN_AGENT_SAMPLE_SECONDS", description="How often the state is looked at (the tunnel's peers, the users, the counters); questions are answered from the last look")
    online_seconds: int = Field(default=180, ge=1, validation_alias="VPN_AGENT_ONLINE_SECONDS", description="A user is online if the tunnel heard from them this recently")
    day_file: Path = Field(default=Path("/run/cascade/day-traffic.json"), validation_alias="VPN_AGENT_DAY_FILE", description="The day's traffic per user, kept for the daily summary across a restart of the agent")
    day_keep_seconds: float = Field(default=300, gt=0, validation_alias="VPN_AGENT_DAY_KEEP_SECONDS", description="How often the day's traffic is written into its file")
    backlog: int = Field(default=128, ge=1, validation_alias="VPN_AGENT_BACKLOG", description="Connections that may wait: the panel's pages and the bot can ask at the same moment")
    # blocking (AdGuard) and the smart-DNS devices
    adguard_url: HttpUrl = Field(default=HttpUrl("http://127.0.0.1:3001/control"), validation_alias="VPN_ADGUARD_URL", description="AdGuard Home's control API")
    adguard_credentials_file: Path = Field(default=Path("/root/awg/adguard-admin.txt"), validation_alias="VPN_ADGUARD_CREDENTIALS_FILE", description="File with AdGuard's admin name and password, as its installer left it")
    adguard_filters_dir: Path = Field(default=Path("/opt/AdGuardHome/data/filters"), validation_alias="VPN_ADGUARD_FILTERS_DIR", description="Where AdGuard keeps the block lists it has downloaded")
    adguard_querylog_file: Path = Field(default=Path("/opt/AdGuardHome/data/querylog.json"), validation_alias="VPN_ADGUARD_QUERYLOG_FILE", description="AdGuard's query log: the day's most visited sites are counted from it, by name only")
    country_lookup_url: HttpUrl | None = Field(default=HttpUrl("https://stat.ripe.net/data/maxmind-geo-lite/data.json"), validation_alias="VPN_COUNTRY_LOOKUP_URL", description="Where an exit's country is asked by its public address (RIPEstat, MaxMind GeoLite); only that address is sent. Empty: no lookup at all")
    country_lookup_timeout: float = Field(default=3.0, gt=0, validation_alias="VPN_COUNTRY_LOOKUP_TIMEOUT", description="Seconds the lookup is waited for; no answer is no country")
    country_backfill_hours: float = Field(default=1, gt=0, validation_alias="VPN_COUNTRY_BACKFILL_HOURS", description="How often the exits without a stored country are looked up again, shortly after the start and then while any has none")
    exit_stats_port: int = Field(default=9101, ge=1, le=65535, validation_alias="VPN_EXIT_STATS_PORT", description="Where the small service on an exit says how the server is doing")
    dns_tokens_file: Path = Field(default=Path("/opt/vpn-stack/lists/dns-tokens.json"), validation_alias="VPN_DNS_TOKENS_FILE", description="The devices' codes, compiled for the DNS gate")
    hagezi_url: HttpUrl = Field(default=HttpUrl(HAGEZI_URL), validation_alias="VPN_HAGEZI_URL", description="Where HaGeZi's lists in AdBlock syntax are (https only): <this>pro.txt, <this>nsfw.txt…")
    group_lists_file: Path = Field(default=Path(GROUP_LISTS_FILE), validation_alias="VPN_GROUP_LISTS_FILE", description="The groups' own lists, made into one file for AdGuard; a path its safe_fs_patterns allow")
    group_lists_refresh_hours: float = Field(default=12, gt=0, validation_alias="VPN_GROUP_LISTS_REFRESH_HOURS", description="How often the lists the groups need are downloaded again")
    group_lists_min_rules: int = Field(default=1000, ge=1, validation_alias="VPN_GROUP_LISTS_MIN_RULES", description="A downloaded list with fewer rules than this is refused (the file in use stays)")
    group_lists_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, validation_alias="VPN_GROUP_LISTS_MAX_BYTES", description="A downloaded list larger than this is refused")
    threats_list_url: HttpUrl = Field(default=HttpUrl(THREATS_LIST_URL), validation_alias="VPN_THREATS_LIST_URL", description="«Угрозы», the list for everybody AdGuard downloads itself; put in when it is missing")
    dns_state_file: Path = Field(default=Path("/opt/vpn-stack/smartdns-data/state.json"), validation_alias="VPN_DNS_STATE_FILE", description="What the DNS gate keeps about the devices it has seen")

    @field_validator("country_lookup_url", mode="before")
    @classmethod
    def lookup_off_when_empty(cls, value: object) -> object:
        """VPN_COUNTRY_LOOKUP_URL= (empty) switches the lookup off."""
        return None if isinstance(value, str) and not value.strip() else value


@lru_cache
def get_settings() -> AgentSettings:
    """The settings of this process: read once, the same object for everybody."""
    return AgentSettings()
