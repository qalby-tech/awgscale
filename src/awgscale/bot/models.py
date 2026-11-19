"""What the bot reads: Telegram's updates and the agent's answers, as objects."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

from awgscale.agent.journal.models import Kind as JournalKind, Who as JournalWho
from awgscale.watch.conf import RouteMode
from awgscale.watch.health import ExitState

ONLINE_FOR = timedelta(seconds=180)        # a user whose last handshake is newer than this is online

Number = int | float                       # written into messages as the agent sent it: 2 stays "2", 0.3 stays "0.3"


def _moment(value: object) -> object:
    """The agent writes "never" as 0 (and a missing moment as null): both are None."""
    return None if value in (0, None, "") else value


def _unix_seconds(moment: datetime) -> float:
    return moment.timestamp()


Moment = Annotated[datetime | None, BeforeValidator(_moment)]
# kept in the stats file as Unix seconds, the way the file has always held it
StoredMoment = Annotated[datetime, PlainSerializer(_unix_seconds, return_type=float)]


class AgentAnswer(BaseModel):
    """Something the agent said. When it does not fit its model, the error names the
    field but never shows what came: answers carry names, addresses and keys."""

    model_config = ConfigDict(hide_input_in_errors=True)


# ── Telegram ─────────────────────────────────────────────────────────────────

class Sender(BaseModel):
    id: int = Field(description="Telegram id of who wrote")


class Chat(BaseModel):
    id: int = Field(description="Telegram id of the chat")


class Message(BaseModel):
    message_id: int = Field(description="Number of the message in its chat")
    chat: Chat = Field(description="Where it was written")
    from_user: Sender | None = Field(default=None, alias="from", description="Who wrote it")
    text: str | None = Field(default=None, description="The text, when the message has one")
    caption: str | None = Field(default=None, description="The words under a picture or a file")


class CallbackQuery(BaseModel):
    """A press on a button under one of the bot's messages."""

    id: str = Field(description="Telegram's id of the press, to answer it")
    from_user: Sender = Field(alias="from", description="Who pressed")
    message: Message | None = Field(default=None, description="The message the button is under")
    data: str | None = Field(default=None, description="What the button carries")


class Update(BaseModel):
    update_id: int = Field(description="Telegram's running number of the update")
    message: Message | None = Field(default=None, description="A new message")
    callback_query: CallbackQuery | None = Field(default=None, description="A pressed button")


class Me(BaseModel):
    username: str | None = Field(default=None, description="The bot's own name, without @")


# ── the agent: the state of the cascade ──────────────────────────────────────

class Client(AgentAnswer):
    """A user on a page the agent answered `clients` with."""

    name: str = Field(description="Name of the user")
    handshake: Moment = Field(default=None, description="The last handshake; None when the user never connected")
    rx: int = Field(description="Bytes the server received from the user (the user's upload)")
    tx: int = Field(description="Bytes the server sent to the user (the user's download)")
    exit: int | None = Field(default=None, description="Index of the exit the user goes through now")
    expires_at: Moment = Field(default=None, description="When the access ends; None is no term")

    def online(self, now: datetime) -> bool:
        return self.handshake is not None and now - self.handshake < ONLINE_FOR


class ClientsPage(AgentAnswer):
    """The agent's answer to `clients`: one page, and how many fit the query in all."""

    total: int = Field(description="How many users fit the query")
    clients: list[Client] = Field(default_factory=list, description="The page")


class TopUser(AgentAnswer):
    name: str = Field(description="Name of the user")
    rx: int = Field(description="Bytes the server received from the user over the day")
    tx: int = Field(description="Bytes the server sent to the user over the day")


class DayTraffic(AgentAnswer):
    """The agent's answer to `day`: the traffic since the day began, counted at every look of the agent."""

    since: Moment = Field(default=None, description="When the day began, as the bot named it")
    counted_from: Moment = Field(default=None, description="When the agent began counting it")
    down: int = Field(default=0, description="Bytes the users downloaded")
    up: int = Field(default=0, description="Bytes the users uploaded")
    active: int = Field(default=0, description="Users seen online")
    top: list[TopUser] = Field(default_factory=list, description="The users who took the most, the most first")


class Counted(AgentAnswer):
    """The users as the state has them: counted, never listed."""

    users: int = Field(default=0, description="Users in all")
    online: int = Field(default=0, description="Online now")


class Exit(AgentAnswer):
    index: int | None = Field(default=None, description="Number of the exit")
    name: str = Field(description="Name of the exit")
    state: ExitState = Field(description="up, down or unknown")
    handshake: Moment = Field(default=None, description="The last handshake of the link to it")
    users: int = Field(default=0, description="How many users are on this exit now")

    @property
    def up(self) -> bool:
        return self.state is ExitState.UP


class Entry(AgentAnswer):
    client_if_up: bool = Field(default=False, description="The users' tunnel is up")
    load1: Number = Field(description="Load average over a minute")
    cpus: Number = Field(description="Processor cores")
    mem_used: Number = Field(description="Memory in use, bytes")
    mem_total: Number = Field(description="Memory in all, bytes")
    disk_pct: Number | None = Field(default=None, description="Disk in use, percent")
    uptime: int = Field(default=0, description="Seconds since the server started")

    @property
    def memory_percent(self) -> int:
        return round(100 * self.mem_used / max(1, self.mem_total))


class BlockedDomain(AgentAnswer):
    domain: str = Field(description="A domain the ad blocker refused")


class Site(AgentAnswer):
    name: str = Field(description="Name of the site")
    count: Number = Field(default=0, description="Requests over the day")


class Dns(AgentAnswer):
    """The ad blocker's day."""

    ok: bool = Field(default=False, description="The blocker answers")
    queries: int = Field(default=0, description="Requests over the day")
    blocked: int = Field(default=0, description="Of them refused")
    top_blocked: list[BlockedDomain] = Field(default_factory=list, description="What is refused most often")
    top_exit: list[Site] = Field(default_factory=list, description="The most asked sites that go abroad")
    top_direct: list[Site] = Field(default_factory=list, description="The most asked sites that go directly")


class Routing(AgentAnswer):
    mode: str | None = Field(default=None, description="What goes abroad: the list, or everything foreign")
    exit_domains: int = Field(default=0, description="Domains of the list")
    exit_nets: int = Field(default=0, description="Networks of the list")

    @property
    def by_list(self) -> bool:
        return self.mode == RouteMode.LIST       # anything else the agent may say means "everything foreign"


class State(AgentAnswer):
    """The cascade as the agent sees it now."""

    time: datetime = Field(description="The server's clock when the state was taken")
    users: Counted = Field(description="The users, counted")
    exits: list[Exit] = Field(description="The exits")
    entry: Entry = Field(description="The entry server itself")
    dns: Dns = Field(default_factory=Dns, description="The ad blocker's day")
    routing: Routing = Field(default_factory=Routing, description="What goes abroad")


# ── the agent: other answers ─────────────────────────────────────────────────

# the kinds of entries that are said on to the admin whoever made them
KINDS_FOR_ADMIN = (JournalKind.TERM, JournalKind.REFUSED, JournalKind.INVITE)


class JournalEntry(AgentAnswer):
    """One line of the change journal."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    t: int | float | str | None = Field(default=None, description="When, as the agent wrote it; only compared")
    who: str | None = Field(default=None, description="Who made the change: panel, bot, system, invite")
    kind: str | None = Field(default=None, description="What kind of entry")
    text: str | None = Field(default=None, description="What happened, in words")
    bad: bool | None = Field(default=False, description="Bad news")

    @property
    def identity(self) -> tuple[int | float | str | None, str | None, str | None, str | None]:
        """What makes two entries the same one (the journal has no ids)."""
        return (self.t, self.who, self.kind, self.text)

    @property
    def for_admin(self) -> bool:
        """Passed on: what was changed in the panel, a user removed by a term, a
        refused save, an invitation used. Not passed on: what this bot did itself,
        the exits going down and up (they have their own alarms), the server's own routine."""
        if self.who == JournalWho.BOT:
            return False
        return self.who == JournalWho.PANEL or self.kind in KINDS_FOR_ADMIN


class DeviceKey(AgentAnswer):
    """What a device needs to connect, as the agent hands it out."""

    conf: str = Field(repr=False, description="The config for AmneziaWG; holds the private key")
    uri: str | None = Field(default=None, repr=False, description="The vpn:// string for AmneziaVPN; holds the private key")
    uri_png: str | None = Field(default=None, repr=False, description="QR picture for AmneziaVPN, base64 PNG; holds the private key")


class Group(AgentAnswer):
    id: str | None = Field(default=None, description="Id of the group")
    name: str = Field(default="", description="Name of the group")


class InviteRequest(BaseModel):
    group: str | None = Field(description="Id of the group the invited join; None is no group")
    uses: int = Field(ge=1, le=10, description="How many devices may use the link")
    days: int = Field(ge=1, le=30, description="For how many days the link works")


# ── the day's counters (stats.json) ──────────────────────────────────────────

class Bucket(BaseModel):
    """What was counted since the last daily summary."""

    since: StoredMoment = Field(description="When the counting started; the agent counts the users' traffic since then (`day`)")
    exit_down: dict[str, float] = Field(default_factory=dict, description="Seconds each exit was not up")
    incidents: int = Field(default=0, description="How many times an exit went down")
    peak_online: int = Field(default=0, description="The most users online at once")
    samples: int = Field(default=0, description="How many samples were folded in")


class DayTotals(BaseModel):
    down: int = Field(description="Bytes the users downloaded")
    up: int = Field(description="Bytes the users uploaded")
    active: int = Field(description="Users seen online")


class HistoryDay(DayTotals):
    day: str = Field(description="The day, YYYY-MM-DD local")


class Stats(BaseModel):
    bucket: Bucket = Field(description="The current day")
    history: list[HistoryDay] = Field(default_factory=list, description="The totals of the last days")
    last_report_day: str | None = Field(default=None, description="The day the last summary was sent for")
    prev_exit_state: dict[str, str] = Field(default_factory=dict, description="The state of each exit at the last sample")

    @classmethod
    def fresh(cls, now: datetime | None = None) -> Stats:
        return cls(bucket=Bucket(since=now or datetime.now(UTC)))
