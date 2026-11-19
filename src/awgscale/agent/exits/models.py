"""What the commands about the exits as servers take and answer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from awgscale.exits.models import Job, Node

from ..cascade.models import EXIT_NAME_RE, MAX_INDEX, MIN_INDEX, CascadeChanged, CascadeSettings, ExitSetting
from ..clients.exits import Registry
from ..errors import Refused
from ..shares import WHOLE

DEFAULT_SSH_PORT = 22
MIN_NEW_SHARE = 1
MAX_NEW_SHARE = 99
DEFAULT_ATTACH_SHARE = 1
LINK_IFACE_PREFIX = "awgk"                 # an exit's interface on the entry follows from its number…
LINK_NETWORK = "10.77.{index}.2"           # …and so does the entry's address inside the link


def link_iface(index: int) -> str:
    return f"{LINK_IFACE_PREFIX}{index}"


def link_address(index: int) -> str:
    return LINK_NETWORK.format(index=index)


def _is_name(value: object) -> bool:
    return isinstance(value, str) and bool(EXIT_NAME_RE.fullmatch(value))


def _is_share(value: object) -> bool:
    return type(value) is int and MIN_NEW_SHARE <= value <= MAX_NEW_SHARE


# ── what is asked ────────────────────────────────────────────────────────────

class NewServer(BaseModel):
    """A rented server that is to become an exit, as the page describes it."""

    model_config = ConfigDict(frozen=True)

    address: str = Field(description="The server's public address")
    name: str = Field(description="How the exit will be called")
    weight: int | None = Field(default=None, description="The percent of the users it takes; None is an equal part")
    ssh_port: int = Field(default=DEFAULT_SSH_PORT, description="Where its SSH listens")

    @classmethod
    def of(cls, data: Mapping[str, Any]) -> NewServer:
        """The description as it came. Refused in the words the page shows."""
        name = data.get("name")
        address = data.get("address")
        ssh_port = data.get("ssh_port", DEFAULT_SSH_PORT)
        if not _is_name(name):
            raise Refused("имя выхода: латиница, цифры, точка, дефис, до 32 знаков")
        if not isinstance(address, str) or type(ssh_port) is not int:
            raise Refused("некорректные данные")
        assert isinstance(name, str)
        return cls.model_construct(address=address, name=name, weight=data.get("weight"), ssh_port=ssh_port)

    def share_among(self, exits_now: int) -> int:
        """The percent the new exit takes: as asked, or an equal part when nothing was said. The first
        exit of a single server takes everybody whatever its number: a lone weight is the whole."""
        share = min(MAX_NEW_SHARE, round(WHOLE / (exits_now + 1))) if self.weight is None else self.weight
        if not _is_share(share):
            raise Refused("доля нового выхода: целое число процентов от 1 до 99")
        return share


class ProvenExit(BaseModel):
    """An exit the exits service has proven to work, as that service names it."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(description="Number of the exit")
    name: str = Field(description="Its name")
    weight: int = Field(default=DEFAULT_ATTACH_SHARE, description="The percent of the users it takes when it is new to the cascade")

    @classmethod
    def of(cls, data: Mapping[str, Any]) -> ProvenExit:
        index = data.get("index")
        name = data.get("name")
        weight = data.get("weight", DEFAULT_ATTACH_SHARE)
        if type(index) is not int or not MIN_INDEX <= index <= MAX_INDEX or not _is_name(name) or not _is_share(weight):
            raise Refused("некорректные данные")
        assert isinstance(name, str)
        return cls(index=index, name=name, weight=weight)

    def setting(self, weight: int) -> ExitSetting:
        """The exit as the cascade's settings will name it: interface and address follow from the number."""
        return ExitSetting(index=self.index, iface=link_iface(self.index), tip=link_address(self.index), name=self.name, weight=weight)


# ── what is answered ─────────────────────────────────────────────────────────

def _seconds(moment: datetime | None) -> int | None:
    """A moment as the page reads it: whole Unix seconds."""
    return None if moment is None else int(moment.timestamp())


def _server_shown(node: Node) -> dict[str, Any]:
    shown = node.wire()
    for moment in ("added", "updated"):
        if getattr(node, moment) is not None:
            shown[moment] = _seconds(getattr(node, moment))
    return shown


def _job_shown(job: Job) -> dict[str, Any]:
    return job.wire() | {"started": _seconds(job.started), "finished": _seconds(job.finished)}


class ServersShown(BaseModel):
    """Everything the page shows about the exits as servers."""

    registry: Registry = Field(description="The exits service's own record of the servers and of its job")

    def wire(self) -> dict[str, Any]:
        registry = self.registry
        return {
            "ok": True,
            "nodes": {index: _server_shown(node) for index, node in registry.nodes.items()},
            "job": _job_shown(registry.job) if registry.job else None,
            "busy": registry.busy,
            "key": registry.key,
        }


class AddStarted(BaseModel):
    """The job that makes a server an exit has begun."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    index: int = Field(description="The number the exit was given")
    name: str = Field(description="Its name")
    address: str = Field(description="The server's address")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class UpdateStarted(BaseModel):
    """The job that updates an exit's server has begun."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    index: int = Field(description="Number of the exit")
    name: str = Field(description="Its name")
    reboot: bool = Field(description="The server restarts after the update")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


class ExitChanged(BaseModel):
    """The cascade's settings changed for one exit: attached, renamed or removed."""

    changed: CascadeChanged = Field(description="The change of the settings itself")
    name: str = Field(description="The exit's name (the new one after a rename)")
    was: str | None = Field(default=None, description="rename: how it was called before")
    index: int | None = Field(default=None, description="remove: its number")
    note: str | None = Field(default=None, description="What the admin should know beside it; said instead of the settings' own note")

    @property
    def cascade(self) -> CascadeSettings:
        return self.changed.cascade

    def wire(self) -> dict[str, Any]:
        beside = self.model_dump(exclude={"changed"}, exclude_none=True)
        return self.changed.wire() | beside
