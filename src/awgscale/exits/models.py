"""The servers behind the exits, the jobs on them, and the requests about both, as objects."""
from __future__ import annotations

import re
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, SecretStr, SerializerFunctionWrapHandler, Strict, model_serializer

from awgscale.core.clock import moment_of
from awgscale.core.links import MAX_EXIT_INDEX
from awgscale.core.patterns import EXIT_NAME_RE

# The interface of the link to an exit:   awgk1   awgk12
IFACE_RE = re.compile(
    r"""
    awgk[0-9]{1,2}
    """,
    re.VERBOSE,
)
JOB_KIND_FIELDS = ("weight", "reboot", "facts", "reboot_needed")       # what only some kinds of job have
DEFAULT_SSH_PORT = 22
MAX_PORT = 65535
MIN_SHARE = 1
MAX_SHARE = 99
MAX_PASSWORD_LENGTH = 200


# ── the pieces requests are made of ──────────────────────────────────────────

def _exit_index(value: int) -> int:
    if not 1 <= value <= MAX_EXIT_INDEX:
        raise ValueError("номер выхода: от 1 до 50")
    return value


def _exit_name(value: str) -> str:
    if not EXIT_NAME_RE.match(value):
        raise ValueError("имя выхода: латиница, цифры, точка, дефис, до 32 знаков")
    return value


def _share(value: int) -> int:
    if not MIN_SHARE <= value <= MAX_SHARE:
        raise ValueError("доля от 1 до 99 процентов, порт SSH от 1 до 65535")
    return value


def _port(value: int) -> int:
    if not 1 <= value <= MAX_PORT:
        raise ValueError("доля от 1 до 99 процентов, порт SSH от 1 до 65535")
    return value


def _password(value: SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    text = value.get_secret_value()
    if not 1 <= len(text) <= MAX_PASSWORD_LENGTH or "\n" in text:
        raise ValueError("пароль: одна строка до 200 знаков")
    return value


def _server_address(value: object) -> IPv4Address:
    try:
        return IPv4Address(value if isinstance(value, str) else "")
    except ValueError:
        raise ValueError("адрес сервера: нужен IPv4-адрес, например 203.0.113.10") from None


def _known_iface(value: object) -> str | None:
    """The interface a caller names; anything that is not a link's interface is as good as not named."""
    return value if isinstance(value, str) and IFACE_RE.fullmatch(value) else None


ExitIndex = Annotated[int, Strict(), AfterValidator(_exit_index)]
ExitName = Annotated[str, Strict(), AfterValidator(_exit_name)]
SharePercent = Annotated[int, Strict(), AfterValidator(_share)]
Port = Annotated[int, Strict(), AfterValidator(_port)]
Password = Annotated[SecretStr | None, AfterValidator(_password)]
ServerAddress = Annotated[IPv4Address, BeforeValidator(_server_address)]
LinkIface = Annotated[str | None, BeforeValidator(_known_iface)]
Moment = Annotated[AwareDatetime, BeforeValidator(moment_of)]


# ── servers ──────────────────────────────────────────────────────────────────

class Node(BaseModel):
    """A server behind an exit, as the service remembers it."""

    model_config = ConfigDict(extra="ignore")

    address: IPv4Address = Field(description="The server's public address")
    ssh_port: int = Field(default=DEFAULT_SSH_PORT, description="Where its SSH listens")
    managed: bool = Field(default=False, description="Made by this service: it may log in. False: set up by hand, never logged into")
    port: int | None = Field(default=None, description="The tunnel's UDP port on the exit")
    iface: str | None = Field(default=None, description="The entry's interface, when it is not awgk<index>")
    added: Moment | None = Field(default=None, description="When the server became an exit")
    updated: Moment | None = Field(default=None, description="When a job on it last finished well")

    @model_serializer(mode="wrap")
    def _without_empty(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """As kept and as the panel gets it: what the server does not have is left out."""
        shape: dict[str, Any] = handler(self)
        return {key: value for key, value in shape.items() if value is not None}

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# ── jobs ─────────────────────────────────────────────────────────────────────

class JobKind(StrEnum):
    ADD = "add"
    UPDATE = "update"
    CLEAN = "clean"


class JobState(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class StepKey(StrEnum):
    LOGIN = "login"
    PREPARE = "prepare"
    LINK = "link"
    TUNNEL = "tunnel"
    FIREWALL = "firewall"
    STATS = "stats"
    ATTACH = "attach"
    UP = "up"
    PACKAGES = "packages"
    REBOOT = "reboot"
    CLEAN = "clean"


class StepState(StrEnum):
    WAIT = "wait"
    RUN = "run"
    DONE = "done"
    FAIL = "fail"
    SKIP = "skip"


class Step(BaseModel):
    key: StepKey = Field(description="Which step of the job")
    title: str = Field(description="What the admin is shown")
    state: StepState = Field(default=StepState.WAIT, description="How far it got")


class Job(BaseModel):
    """A job as the panel shows it. The secret never goes into it."""

    id: str = Field(description="When it started and for which exit")
    kind: JobKind = Field(description="What is being done")
    index: int = Field(description="Number of the exit")
    name: str = Field(default="", description="Name of the exit")
    address: IPv4Address = Field(description="The server's public address")
    state: JobState = Field(default=JobState.RUNNING, description="Running, done or failed")
    error: str | None = Field(default=None, description="Why it failed, in words for the admin")
    started: Moment = Field(description="When it started")
    finished: Moment | None = Field(default=None, description="When it ended; None while it runs")
    steps: list[Step] = Field(default_factory=list, description="Its steps, in order")
    weight: int | None = Field(default=None, description="add: the percent of the users the new exit takes")
    reboot: bool | None = Field(default=None, description="update: restart the server")
    facts: str | None = Field(default=None, description="What the server said about itself")
    reboot_needed: bool | None = Field(default=None, description="update: the new kernel waits for a restart")

    @model_serializer(mode="wrap")
    def _without_other_kinds(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """What a kind of job does not have is left out rather than shown empty."""
        shape: dict[str, Any] = handler(self)
        for name in JOB_KIND_FIELDS:
            if getattr(self, name) is None:
                shape.pop(name, None)
        return shape

    def wire(self) -> dict[str, Any]:
        """As JSON."""
        return self.model_dump(mode="json")


# ── answers ──────────────────────────────────────────────────────────────────

class Started(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    started: bool = Field(default=True, description="The job is running; the panel follows it in the nodes")


class Removed(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    cleaning: bool = Field(description="A job cleans the server itself now")


class Adopted(BaseModel):
    ok: bool = Field(default=True, description="Always true: a refusal has another shape")


class NodesAnswer(BaseModel):
    """Everything the panel shows about the exits as servers."""

    ok: bool = Field(default=True, description="Always true")
    nodes: dict[str, Node] = Field(description="The servers by the number of their exit")
    job: Job | None = Field(description="The last job, running or ended; None before the first")
    busy: bool = Field(description="A job runs now")
    key: str = Field(description="The line for an exit's authorized_keys")


# ── requests ─────────────────────────────────────────────────────────────────

class AddRequest(BaseModel):
    index: ExitIndex = Field(description="Number of the new exit")
    name: ExitName = Field(description="Its name: latin letters, digits, dot, hyphen, up to 32")
    address: ServerAddress = Field(description="The rented server's public IPv4 address")
    weight: SharePercent = Field(default=MIN_SHARE, description="The percent of the users the new exit takes")
    ssh_port: Port = Field(default=DEFAULT_SSH_PORT, description="Where the server's SSH listens")
    password: Password = Field(default=None, description="The root password: used once to put the key there, never kept or logged")


class UpdateRequest(BaseModel):
    index: ExitIndex = Field(description="Number of the exit")
    reboot: bool = Field(default=False, description="Restart the server after the update")
    name: str = Field(default="", description="Name of the exit, for the job's record")


class RemoveRequest(BaseModel):
    index: ExitIndex = Field(description="Number of the exit")
    iface: LinkIface = Field(default=None, description="Its interface on the entry: an exit made before this service may sit on another number's")
    name: str = Field(default="", description="Name of the exit, for the job's record")


class AdoptRequest(BaseModel):
    index: ExitIndex = Field(description="Number of the exit")
    address: ServerAddress = Field(description="The server's public IPv4 address")
    managed: bool = Field(default=False, description="May this service log into it")
    iface: LinkIface = Field(default=None, description="Its interface on the entry, when it is not awgk<index>")
    ssh_port: Port = Field(default=DEFAULT_SSH_PORT, description="Where the server's SSH listens")
