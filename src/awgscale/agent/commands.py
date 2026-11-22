"""The commands the socket takes, and the model each one's arguments must fit.

A request is a list of strings: the command's name, then its arguments in
order. The panel's container is the less trusted side, so nothing else is let
through: only these commands, with strictly formatted arguments. What a
payload (base64 of JSON) holds is checked by the command itself; here only its
shape and size.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self, Sequence, get_args, get_origin

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, Strict, ValidationError

from awgscale.core.patterns import EXIT_NAME_RE, HOST_RE, NAME_RE, URL_PATH_RE
from awgscale.watch.conf import RouteMode

# A term:   12h   7d   4w   — hours, days or weeks, 1…999 of them
TERM_RE = re.compile(
    r"""
    ^[1-9][0-9]{0,2}      # 12
    [hdw]\Z               # h
    """,
    re.VERBOSE,
)
# A payload, base64 of JSON:   eyJhIjoxfQ==
BASE64_RE = re.compile(
    r"""
    ^[A-Za-z0-9+/]+       # eyJhIjoxfQ
    ={0,2}\Z              # ==
    """,
    re.VERBOSE,
)
# ID_RE and INVITE_RE stay on one line: the suites compare their text with the old daemon's.
# the id of a group, a space or an invitation:   g00001
ID_RE = re.compile(r"^[a-z0-9]{6}\Z")
# the secret of an invitation:   aB3_-aB3_-aB3_-aB3_-Zz
INVITE_RE = re.compile(r"^[A-Za-z0-9_-]{22}\Z")
# An exit's number:   1   12
EXIT_INDEX_RE = re.compile(
    r"""
    ^[1-9][0-9]?\Z
    """,
    re.VERBOSE,
)
# A country by its ISO 3166 two-letter code:   ru   kz
COUNTRY_RE = re.compile(
    r"""
    ^[a-z]{2}\Z
    """,
    re.VERBOSE,
)
# An address inside the tunnel, as the panel saw the app's request come from:   172.16.17.14
ADDRESS_RE = re.compile(
    r"""
    ^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}   # 172.16.17.
    (?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\Z           # 14
    """,
    re.VERBOSE,
)

# A moment as Unix seconds:   1763300000
SECONDS_RE = re.compile(
    r"""
    ^[1-9][0-9]{0,10}\Z
    """,
    re.VERBOSE,
)

MAX_NAMES = 60
NO_TERM = "none"
AUTO_EXIT = "auto"


class Command(StrEnum):
    STATE = "state"
    CLIENTS = "clients"
    DAY = "day"
    TRAFFIC = "traffic"
    JOURNAL = "journal"
    ADD = "add"
    REMOVE = "remove"
    EXPIRE = "expire"
    QR = "qr"
    DIAG = "diag"
    SUSPEND = "suspend"
    USER_EXIT = "user-exit"
    UGROUPS_GET = "ugroups-get"
    UGROUPS_SET = "ugroups-set"
    SPACES_GET = "spaces-get"
    SPACES_SET = "spaces-set"
    SITES_GET = "sites-get"
    SITES_SET = "sites-set"
    SITES_REFRESH = "sites-refresh"
    MODE_SET = "mode-set"
    CHECK = "check"
    PROBE = "probe"
    BLOCK_GET = "block-get"
    BLOCK_SET = "block-set"
    BLOCK_LISTS = "block-lists"
    RULE_ROUTE = "rule-route"
    DNS_LIST = "dns-list"
    DNS_ADD = "dns-add"
    DNS_REMOVE = "dns-remove"
    INVITE_LIST = "invite-list"
    INVITE_ADD = "invite-add"
    INVITE_REMOVE = "invite-remove"
    INVITE_INFO = "invite-info"
    INVITE_REDEEM = "invite-redeem"
    EXITS_GET = "exits-get"
    EXIT_ADD = "exit-add"
    EXIT_ATTACH = "exit-attach"
    EXIT_RENAME = "exit-rename"
    EXIT_REMOVE = "exit-remove"
    EXIT_UPDATE = "exit-update"
    WEIGHTS_SET = "weights-set"
    COUNTRY_SET = "country-set"
    APP_ME = "app-me"
    APP_EXITS = "app-exits"
    APP_EXIT = "app-exit"
    APP_FILTER = "app-filter"
    APP_ROUTE = "app-route"


class Switch(StrEnum):
    ON = "on"
    OFF = "off"


def _matching(pattern: re.Pattern[str]):
    def check(value: str) -> str:
        if not pattern.match(value):
            raise ValueError("does not fit")
        return value
    return AfterValidator(check)


def _payload(max_length: int):
    def check(value: str) -> str:
        if not BASE64_RE.match(value) or len(value) > max_length:
            raise ValueError("does not fit")
        return value
    return AfterValidator(check)


def _exit_choice(value: str) -> str:
    if value != AUTO_EXIT and not EXIT_INDEX_RE.match(value):
        raise ValueError("does not fit")
    return value


def _term_or_none(value: str) -> str:
    if value != NO_TERM and not TERM_RE.match(value):
        raise ValueError("does not fit")
    return value


UserName = Annotated[str, Strict(), _matching(NAME_RE)]
Term = Annotated[str, Strict(), _matching(TERM_RE)]
TermOrNone = Annotated[str, Strict(), AfterValidator(_term_or_none)]
Host = Annotated[str, Strict(), _matching(HOST_RE)]
UrlPath = Annotated[str, Strict(), _matching(URL_PATH_RE)]
ListId = Annotated[str, Strict(), _matching(ID_RE)]
InviteToken = Annotated[str, Strict(), _matching(INVITE_RE)]
ExitIndex = Annotated[str, Strict(), _matching(EXIT_INDEX_RE)]
ExitName = Annotated[str, Strict(), _matching(EXIT_NAME_RE)]
Country = Annotated[str, Strict(), _matching(COUNTRY_RE)]
Address = Annotated[str, Strict(), _matching(ADDRESS_RE)]
ExitChoice = Annotated[str, Strict(), AfterValidator(_exit_choice)]
Names = Annotated[list[UserName], Field(min_length=1, max_length=MAX_NAMES)]
SmallPayload = Annotated[str, Strict(), _payload(400)]
ExitPayload = Annotated[str, Strict(), _payload(800)]
SharesPayload = Annotated[str, Strict(), _payload(2000)]
SettingsPayload = Annotated[str, Strict(), _payload(120000)]
QueryPayload = Annotated[str, Strict(), _payload(8000)]
Seconds = Annotated[str, Strict(), _matching(SECONDS_RE)]


def _is_list(annotation: object) -> bool:
    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return get_origin(annotation) is list


class CommandRequest(BaseModel):
    """The arguments of one command. Fields are the arguments in their order; a
    list takes all that are left; a field that may be None may be left out at the end."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: ClassVar[StrEnum]         # a command of the socket (Command), or one that only a shell may run (shell.ShellCommand)

    @classmethod
    def from_args(cls, arguments: Sequence[str]) -> Self:
        """→ the request. Raises ValidationError or ValueError when the arguments do not fit."""
        left = list(arguments)
        values: dict[str, object] = {}
        for name, field in cls.model_fields.items():
            if _is_list(field.annotation):
                values[name] = left
                left = []
            elif left:
                values[name] = left.pop(0)
        if left:
            raise ValueError("too many arguments")
        return cls.model_validate(values)

    def to_args(self) -> list[str]:
        """The request as the list of strings it came as."""
        arguments: list[str] = [self.command.value]
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, list):
                arguments.extend(str(item) for item in value)
            elif value is not None:
                arguments.append(str(value.value if isinstance(value, StrEnum) else value))
        return arguments


class NoArguments(CommandRequest):
    pass


class State(NoArguments):
    command = Command.STATE


class Traffic(NoArguments):
    command = Command.TRAFFIC


class Clients(CommandRequest):
    command = Command.CLIENTS

    payload: QueryPayload = Field(description="Which users, in which order, which page; base64 of JSON (state.clients.ClientsQuery)")


class DayTraffic(CommandRequest):
    command = Command.DAY

    since: Seconds = Field(description="When the asker's day began, Unix seconds")


class Journal(NoArguments):
    command = Command.JOURNAL


class UgroupsGet(NoArguments):
    command = Command.UGROUPS_GET


class SpacesGet(NoArguments):
    command = Command.SPACES_GET


class SitesGet(NoArguments):
    command = Command.SITES_GET


class BlockGet(NoArguments):
    command = Command.BLOCK_GET


class DnsList(NoArguments):
    command = Command.DNS_LIST


class InviteList(NoArguments):
    command = Command.INVITE_LIST


class ExitsGet(NoArguments):
    command = Command.EXITS_GET


class Add(CommandRequest):
    command = Command.ADD

    name: UserName = Field(description="Name of the new user")
    expires: Term | None = Field(default=None, description='Term of access counted from now, like "7d"')


class Remove(CommandRequest):
    command = Command.REMOVE

    names: Names = Field(description="Whom to remove")


class Expire(CommandRequest):
    command = Command.EXPIRE

    expires: TermOrNone = Field(description='The new term counted from now, or "none"')
    names: Names = Field(description="Whose term changes")


class Qr(CommandRequest):
    command = Command.QR

    name: UserName = Field(description="Whose connection code")


class Diag(CommandRequest):
    command = Command.DIAG

    name: UserName = Field(description="Whom to check")


class Suspend(CommandRequest):
    command = Command.SUSPEND

    switch: Switch = Field(description="on pauses, off resumes")
    names: Names = Field(description="Whom")


class UserExit(CommandRequest):
    command = Command.USER_EXIT

    exit: ExitChoice = Field(description='"auto", or the number of the exit the users are kept on by hand')
    names: Names = Field(description="Whom")


class UgroupsSet(CommandRequest):
    command = Command.UGROUPS_SET

    payload: SettingsPayload = Field(description="The groups of users, base64 of JSON")


class SpacesSet(CommandRequest):
    command = Command.SPACES_SET

    payload: SettingsPayload = Field(description="The device spaces, base64 of JSON")


class SitesSet(CommandRequest):
    command = Command.SITES_SET

    payload: SettingsPayload = Field(description="The sites that go abroad, base64 of JSON")


class BlockSet(CommandRequest):
    command = Command.BLOCK_SET

    payload: SettingsPayload = Field(description="What is blocked, base64 of JSON")


class BlockLists(CommandRequest):
    command = Command.BLOCK_LISTS

    payload: SettingsPayload = Field(description="A change of the block lists by link, base64 of JSON")


class SitesRefresh(CommandRequest):
    command = Command.SITES_REFRESH

    list_id: ListId | None = Field(default=None, description="One list by link; all that are switched on when left out")


class RuleRoute(StrEnum):
    """Where a rule (a group of sites, or of blocked names) leads."""

    EXIT = "exit"
    BLOCK = "block"
    OFF = "off"


class RuleRoutesSet(CommandRequest):
    command = Command.RULE_ROUTE

    payload: SettingsPayload = Field(description='Where rules lead, base64 of JSON: {"changes": [{"rule": id, "route": "exit" | "block" | "off"}, …]}')


class ModeSet(CommandRequest):
    command = Command.MODE_SET

    mode: RouteMode = Field(description="What goes abroad")


class Check(CommandRequest):
    command = Command.CHECK

    host: Host = Field(description="The name to look up: where it goes, what blocks it")


class Probe(CommandRequest):
    command = Command.PROBE

    host: Host = Field(description="The site to open directly and through every exit")
    path: UrlPath | None = Field(default=None, description="The page of it to ask for")


class DnsAdd(CommandRequest):
    command = Command.DNS_ADD

    name: UserName = Field(description="Name of the new smart-DNS device")


class DnsRemove(CommandRequest):
    command = Command.DNS_REMOVE

    name: UserName = Field(description="The device whose address is revoked")


class InviteAdd(CommandRequest):
    command = Command.INVITE_ADD

    payload: SmallPayload = Field(description="For which group, how many devices, for how long; base64 of JSON")


class InviteRemove(CommandRequest):
    command = Command.INVITE_REMOVE

    invite_id: ListId = Field(description="The invitation to revoke")


class InviteInfo(CommandRequest):
    command = Command.INVITE_INFO

    token: InviteToken = Field(description="The secret of the invitation link")


class InviteRedeem(CommandRequest):
    command = Command.INVITE_REDEEM

    token: InviteToken = Field(description="The secret of the invitation link")
    name: UserName = Field(description="The name the guest gives the device")


class ExitAdd(CommandRequest):
    command = Command.EXIT_ADD

    payload: ExitPayload = Field(description="Address, name, share and SSH port of the rented server; base64 of JSON")


class ExitAttach(CommandRequest):
    command = Command.EXIT_ATTACH

    payload: ExitPayload = Field(description="The exit the exits service has proven to work; base64 of JSON")


class ExitRename(CommandRequest):
    command = Command.EXIT_RENAME

    index: ExitIndex = Field(description="Number of the exit")
    name: ExitName = Field(description="Its new name")


class ExitRemove(CommandRequest):
    command = Command.EXIT_REMOVE

    index: ExitIndex = Field(description="Number of the exit")
    force: Literal["force"] | None = Field(default=None, description="Remove although it is the last one that works")


class ExitUpdate(CommandRequest):
    command = Command.EXIT_UPDATE

    index: ExitIndex = Field(description="Number of the exit")
    reboot: Literal["reboot"] | None = Field(default=None, description="Restart the server after the update")


class WeightsSet(CommandRequest):
    command = Command.WEIGHTS_SET

    payload: SharesPayload = Field(description="The shares of the exits and nothing else of the cascade's settings; base64 of JSON")


class CountrySet(CommandRequest):
    command = Command.COUNTRY_SET

    country: Country = Field(description="The home country: its networks go direct (ISO 3166 two-letter code, lower case)")


class AppMe(CommandRequest):
    command = Command.APP_ME

    address: Address = Field(description="The tunnel address the app's request came from: it says whose device it is")


class AppExits(NoArguments):
    command = Command.APP_EXITS


class AppExit(CommandRequest):
    command = Command.APP_EXIT

    address: Address = Field(description="The tunnel address the app's request came from")
    exit: ExitChoice = Field(description='"auto", or the number of the exit the user wants to be kept on')


# How a user may filter their own requests: a level, or "auto" (as their group)
AUTO_FILTER = "auto"
OwnFilterChoice = Literal["std", "off", "kids", "pro", "proplus", "ultimate", "auto"]


class AppFilter(CommandRequest):
    command = Command.APP_FILTER

    address: Address = Field(description="The tunnel address the app's request came from")
    filter: OwnFilterChoice = Field(description='"auto" (as the group), or the filtering the user chooses for themselves')


class RouteChoice(StrEnum):
    """Where a user's traffic goes: as the network's mode says, or all of it through their exit."""

    SMART = "smart"
    ALL = "all"


class AppRoute(CommandRequest):
    command = Command.APP_ROUTE

    address: Address = Field(description="The tunnel address the app's request came from")
    route: RouteChoice = Field(description='"smart" (as the network decides) or "all" (everything through the exit, Russian sites too)')


REQUESTS: dict[Command, type[CommandRequest]] = {
    model.command: model
    for model in (
        State, Clients, DayTraffic, Traffic, Journal, Add, Remove, Expire, Qr, Diag, Suspend, UserExit, UgroupsGet, UgroupsSet, SpacesGet, SpacesSet,
        SitesGet, SitesSet, SitesRefresh, ModeSet, Check, Probe, BlockGet, BlockSet, BlockLists, DnsList, DnsAdd, DnsRemove,
        InviteList, InviteAdd, InviteRemove, InviteInfo, InviteRedeem, ExitsGet, ExitAdd, ExitAttach, ExitRename, ExitRemove,
        ExitUpdate, WeightsSet, CountrySet, AppMe, AppExits, AppExit, AppFilter,
        AppRoute,
        RuleRoutesSet,
    )
}


def parse(arguments: object) -> CommandRequest | None:
    """A request as it came from the socket → the model of its command; None when it is not one this agent takes."""
    if not isinstance(arguments, list) or not arguments or not all(isinstance(argument, str) for argument in arguments):
        return None
    try:
        model = REQUESTS[Command(arguments[0])]
        return model.from_args(arguments[1:])
    except (ValueError, ValidationError):
        return None
