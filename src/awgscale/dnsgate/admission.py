"""Who is allowed: the devices' personal codes, and the addresses those codes were seen from."""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from awgscale.core.clock import utc_now

from .settings import DnsgateSettings

log = logging.getLogger("awgscale.dnsgate")

Clock = Callable[[], datetime]
UNNAMED = "?"


class Device(BaseModel):
    """What is known of a device that used its code (shown in the panel)."""

    ip: str = Field(description="The address its last request came from")
    last: datetime = Field(description="When its last request came")
    queries: int = Field(default=0, ge=0, description="How many requests it has made")


class SavedDevice(BaseModel):
    """A device as the state file holds one: the agent reads this shape."""

    ip: str | None = Field(default=None, description="The address its last request came from")
    last: int | None = Field(default=None, description="Unix time of its last request")
    queries: int = Field(default=0, description="How many requests it has made")


class SavedState(BaseModel):
    """The state file: the agent reads `allowed` and `seen` from it."""

    allowed: dict[str, float] = Field(default_factory=dict, description="Address → Unix time until which the proxy admits it")
    seen: dict[str, SavedDevice] = Field(default_factory=dict, description="Device name → what is known of it")
    saved: int = Field(default=0, description="Unix time of the save")


class Admission:
    def __init__(self, settings: DnsgateSettings, clock: Clock = utc_now) -> None:
        self.__settings = settings
        self.__clock = clock
        self.__codes: dict[str, str] = {}                 # personal code → device name
        self.__allowed: dict[str, datetime] = {}          # client address → until when the proxy admits it
        self.__seen: dict[str, Device] = {}               # device name → what is known of it

    # ── the codes ────────────────────────────────────────────────────────────

    def load_codes(self) -> None:
        """Reads the codes again. A file that cannot be read leaves them as they were."""
        try:
            tokens = json.loads(self.__settings.tokens_file.read_text()).get("tokens", {})
            self.__codes = {code: device.get("name", UNNAMED) for code, device in tokens.items()}
        except (OSError, ValueError, AttributeError):
            return
        log.info("codes loaded: %d", len(self.__codes))

    # ── asking ───────────────────────────────────────────────────────────────

    def admit(self, ip: str, code: str | None) -> bool:
        """May this DoH request be answered; a valid code also (re)admits its address to the proxy."""
        name = self.__codes.get(code) if code else None
        if name is None:
            return ip in self.__settings.static_allow
        now = self.__clock()
        self.__allowed[ip] = now + self.__settings.allow_for
        known = self.__seen.get(name)
        self.__seen[name] = Device(ip=ip, last=now, queries=(known.queries if known else 0) + 1)
        return True

    def proxy_admits(self, ip: str) -> bool:
        if ip in self.__settings.static_allow:
            return True
        until = self.__allowed.get(ip)
        return until is not None and until > self.__clock()

    # ── between restarts ─────────────────────────────────────────────────────

    def load_state(self) -> None:
        try:
            saved = SavedState.model_validate_json(self.__settings.state_file.read_text())
        except (OSError, ValidationError):
            return
        now = self.__clock()
        for ip, until_seconds in saved.allowed.items():
            until = datetime.fromtimestamp(until_seconds, UTC)
            if until > now:
                self.__allowed[ip] = until
        for name, device in saved.seen.items():
            if device.ip is not None and device.last is not None:
                self.__seen[name] = Device(ip=device.ip, last=datetime.fromtimestamp(device.last, UTC), queries=device.queries)

    def __forget_stale(self) -> None:
        now = self.__clock()
        self.__allowed = {ip: until for ip, until in self.__allowed.items() if until > now}
        names = set(self.__codes.values())
        for gone in [name for name in self.__seen if name not in names]:
            # a revoked code also loses the proxy admission it earned
            self.__allowed.pop(self.__seen[gone].ip, None)
            del self.__seen[gone]

    async def save_state(self) -> None:
        """Forgets what is over or revoked and writes the rest (beside, then moved into place).
        A coroutine, so the scheduler runs it on the event loop, where the admissions are made."""
        self.__forget_stale()
        state = SavedState(
            allowed={ip: until.timestamp() for ip, until in self.__allowed.items()},
            seen={name: SavedDevice(ip=device.ip, last=int(device.last.timestamp()), queries=device.queries) for name, device in self.__seen.items()},
            saved=int(self.__clock().timestamp()),
        )
        path = self.__settings.state_file
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(state.model_dump_json())
            os.replace(temporary, path)
        except OSError:
            pass
