"""The cascade's settings as an object, and the answers of the commands that change them."""
from __future__ import annotations

import ipaddress
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

# An interface named in cascade.conf:   awg0   awgk1
IFACE_RE = re.compile(
    r"""
    [a-z][a-z0-9]{1,14}
    """,
    re.VERBOSE,
)
# An exit's name:   exit1-nl   nl.2
EXIT_NAME_RE = re.compile(
    r"""
    [A-Za-z0-9._-]{1,32}
    """,
    re.VERBOSE,
)
# A country by its ISO 3166 two-letter code:   ru   kz
COUNTRY_RE = re.compile(
    r"""
    [a-z]{2}
    """,
    re.VERBOSE,
)
DEFAULT_HOME_COUNTRY = "ru"
MIN_EXITS = 1
MAX_EXITS = 8
MIN_INDEX = 1
MAX_INDEX = 50
MIN_WEIGHT = 1
MAX_WEIGHT = 100
MIN_SUBNET_PREFIX = 16
MAX_SUBNET_PREFIX = 30
MAX_ADDRESSES = 16
MAX_RU_NETS = 64
SHOWN_BAD_ADDRESS = 40
DEFAULT_WEIGHT = 1
UNIQUE_AMONG_EXITS = {"index": "номер", "iface": "интерфейс", "tip": "адрес туннеля", "name": "имя"}


class ExitSetting(BaseModel):
    """One exit as the settings name it."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(description="The exit's number: its routing table and mark are made from it")
    iface: str = Field(description="The interface of the link to this exit")
    tip: str = Field(description="The entry's address inside that link")
    name: str = Field(description="How the exit is called on the pages")
    weight: int = Field(default=DEFAULT_WEIGHT, description="Its share of the users, relative to the others")


class CascadeSettings(BaseModel):
    """The document. Addresses are kept as the admin wrote them: the file is compiled from these very strings."""

    model_config = ConfigDict(frozen=True)

    client_if: str = Field(description="The users' tunnel interface")
    client_subnet: str = Field(description="The users' network")
    udp_via_exit: bool = Field(description="In list mode: also send UDP, except port 443, to non-Russian addresses abroad")
    exits: tuple[ExitSetting, ...] = Field(description="The exits, in their order")
    dns_via_exit: tuple[str, ...] = Field(default=(), description="This server's own DNS upstreams, resolved through an exit")
    via_exit_subnets: tuple[str, ...] = Field(default=(), description="Local Docker subnets whose outgoing traffic leaves through an exit")
    probe_targets: tuple[str, ...] = Field(default=(), description="Health-probe targets, pinged through each exit")
    extra_ru_nets: tuple[str, ...] = Field(default=(), description="Addresses to force through the Russian side despite the list")
    single_server: bool = Field(default=False, description="A single server: no exits, the users' internet leaves from this server")
    home_country: str = Field(default=DEFAULT_HOME_COUNTRY, description="The country whose networks go direct (ISO 3166 two-letter code, lower case)")

    @model_serializer(mode="wrap")
    def without_cascade_default(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """`single_server` and `home_country` only when they differ from what a server always was,
        wherever the settings are shown: a Russian cascade's document stays what it was."""
        shown: dict[str, Any] = handler(self)
        if not self.single_server:
            shown.pop("single_server", None)
        if self.home_country == DEFAULT_HOME_COUNTRY:
            shown.pop("home_country", None)
        return shown

    def wire(self) -> dict[str, Any]:
        """As it is stored and answered: plain JSON, lists where the model keeps tuples."""
        return self.model_dump(mode="json")

    def weights(self) -> dict[str, int]:
        """The shares as the page names them: by the exit's number, as text."""
        return {str(exit_.index): exit_.weight for exit_ in self.exits}

    def with_weights(self, weights: dict[str, int]) -> CascadeSettings:
        exits = tuple(exit_.model_copy(update={"weight": weights.get(str(exit_.index), exit_.weight)}) for exit_ in self.exits)
        return self.model_copy(update={"exits": exits})


def _addresses(document: dict[str, Any], key: str, networks: bool, most: int = MAX_ADDRESSES) -> tuple[str, ...]:
    items = document.get(key)
    if not isinstance(items, list) or len(items) > most or not all(type(item) is str for item in items):
        raise ValueError(f"{key}: нужен список адресов")
    for item in items:
        try:
            if networks:
                ipaddress.IPv4Network(item, strict=False)
            else:
                ipaddress.IPv4Address(item)
        except ValueError:
            raise ValueError(f"{key}: «{item[:SHOWN_BAD_ADDRESS]}» — не адрес") from None
    return tuple(items)


def _exit(record: object, client_if: str) -> ExitSetting:
    index_fits = isinstance(record, dict) and type(record.get("index")) is int and MIN_INDEX <= record["index"] <= MAX_INDEX
    if not index_fits or type(record.get("weight")) is not int or not MIN_WEIGHT <= record["weight"] <= MAX_WEIGHT:
        raise ValueError("выход: номер от 1 до 50 и доля от 1 до 100")
    assert isinstance(record, dict)
    if type(record.get("iface")) is not str or not IFACE_RE.fullmatch(record["iface"]) or record["iface"] == client_if:
        raise ValueError(f"выход {record['index']}: не имя интерфейса")
    if type(record.get("name")) is not str or not EXIT_NAME_RE.fullmatch(record["name"]):
        raise ValueError(f"выход {record['index']}: имя — латиница, цифры, точка, дефис, до 32 знаков")
    try:
        if type(record.get("tip")) is not str:
            raise ValueError
        ipaddress.IPv4Address(record["tip"])
    except ValueError:
        raise ValueError(f"выход {record['index']}: адрес туннеля — не адрес") from None
    return ExitSetting(index=record["index"], iface=record["iface"], tip=record["tip"], name=record["name"], weight=record["weight"])


def checked(document: object) -> CascadeSettings:
    """A document as it came → the settings. Raises ValueError (in the admin's
    language) for what must never reach the file the server boots from."""
    if not isinstance(document, dict):
        raise ValueError("настройки каскада: не тот вид")
    client_if = document.get("client_if")
    client_subnet = document.get("client_subnet")
    if type(client_if) is not str or not IFACE_RE.fullmatch(client_if):
        raise ValueError("client_if: не имя интерфейса")
    try:
        if type(client_subnet) is not str or "/" not in client_subnet:
            raise ValueError
        if not MIN_SUBNET_PREFIX <= ipaddress.IPv4Network(client_subnet).prefixlen <= MAX_SUBNET_PREFIX:
            raise ValueError
    except ValueError:
        raise ValueError("client_subnet: не сеть") from None
    if type(document.get("udp_via_exit")) is not bool:
        raise ValueError("udp_via_exit: да или нет")
    single_server = document.get("single_server", False)
    if type(single_server) is not bool:
        raise ValueError("single_server: да или нет")
    home_country = document.get("home_country", DEFAULT_HOME_COUNTRY)
    if type(home_country) is not str or not COUNTRY_RE.fullmatch(home_country):
        raise ValueError("home_country: код страны из двух строчных латинских букв, например ru или kz")
    records = document.get("exits")
    if single_server:
        if records != []:
            raise ValueError("single_server: у сервера без выходов список выходов пуст")
    elif not isinstance(records, list) or not MIN_EXITS <= len(records) <= MAX_EXITS:
        raise ValueError("нужен хотя бы один выход (и не больше восьми): без выхода туннель при загрузке не поднимется")
    assert isinstance(records, list)
    exits = tuple(_exit(record, client_if) for record in records)
    for field, called in UNIQUE_AMONG_EXITS.items():
        if len({getattr(exit_, field) for exit_ in exits}) != len(exits):
            raise ValueError("у двух выходов совпадает " + called)
    return CascadeSettings(
        client_if=client_if,
        client_subnet=client_subnet,
        udp_via_exit=document["udp_via_exit"],
        exits=exits,
        dns_via_exit=_addresses(document, "dns_via_exit", networks=False),
        via_exit_subnets=_addresses(document, "via_exit_subnets", networks=True),
        probe_targets=_addresses(document, "probe_targets", networks=False),
        extra_ru_nets=_addresses(document, "extra_ru_nets", networks=True, most=MAX_RU_NETS),
        single_server=single_server,
        home_country=home_country,
    )


class CascadeChanged(BaseModel):
    """The answer of a command that changes the settings."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    already: bool | None = Field(default=None, description="Set when nothing had to change")
    cascade: CascadeSettings = Field(description="The settings as they are now")
    note: str | None = Field(default=None, description="What the admin should know beside it")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class CascadeShown(BaseModel):
    """The answer of cascade-get."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    cascade: CascadeSettings = Field(description="The stored settings")
    compiled: bool = Field(description="The file on the host says exactly what the settings say")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CascadeFile(BaseModel):
    """The answer of cascade-get --conf: the file as it would be compiled."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    conf: str = Field(description="The text of cascade.conf made from the stored settings")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()
