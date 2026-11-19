"""An interface file (awg-quick's format) as this service reads it."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .settings import Role

# awg-quick's own keys of [Interface]: not for `awg setconf`
QUICK_KEYS = frozenset({"address", "dns", "mtu", "table", "preup", "postup", "predown", "postdown", "saveconfig"})
INTERFACE_SECTION = "[interface]"
TABLE_OFF = "off"
# One IPv4 address with its mask:  172.16.17.1/24
ADDRESS_RE = re.compile(
    r"""
    (?P<address>\d+\.\d+\.\d+\.\d+)   # 172.16.17.1
    /(?P<mask>\d+)                    # /24
    """,
    re.VERBOSE,
)


class Refused(Exception):
    """The file is not something this service brings up."""


class InterfaceFile(BaseModel):
    """What this service takes from an interface file."""

    model_config = ConfigDict(frozen=True)

    address: str = Field(description="The interface's one IPv4 address with its mask")
    mtu: int | None = Field(description="The MTU, when the file names one")
    port: int | None = Field(description="The port it listens on; a link to an exit dials out and has none")
    private_key: SecretStr = Field(description="The interface's private key; never logged or shown in repr")
    postup: tuple[str, ...] = Field(description="The PostUp lines: read for the rules they stand for, never executed")
    setconf: SecretStr = Field(description="What `awg-quick strip` gives — every line but awg-quick's own; it holds the keys")

    def fingerprint(self) -> str:
        """Of what `awg setconf` would be given — never the text itself, it holds the keys.
        Blank lines at the end do not count."""
        return hashlib.sha256(self.setconf.get_secret_value().rstrip("\n").encode()).hexdigest()


def _first(values: Mapping[str, Sequence[str]], key: str) -> str:
    found = values.get(key)
    return found[0] if found else ""


def read_interface_file(path: Path, role: Role) -> InterfaceFile:
    """Raises OSError (cannot be read) or Refused (not ours to bring up)."""
    values: dict[str, list[str]] = {}
    kept: list[str] = []
    section = None
    for line in path.read_text().splitlines():
        meaning = line.split("#", 1)[0].strip()
        if meaning.startswith("[") and meaning.endswith("]"):
            section = meaning.lower()
        key = meaning.split("=", 1)[0].strip().lower() if "=" in meaning else ""
        if section == INTERFACE_SECTION and key:
            values.setdefault(key, []).append(meaning.split("=", 1)[1].strip())
            if key in QUICK_KEYS:
                continue
        kept.append(line)

    private_key = _first(values, "privatekey")
    address = _first(values, "address")
    port = _first(values, "listenport")
    mtu = _first(values, "mtu")
    table = _first(values, "table")
    if not private_key or not address or (role is Role.CLIENTS and not port.isdigit()):
        raise Refused(f"{path}: нет ключа, адреса или порта")
    if "," in address or ":" in address or not ADDRESS_RE.fullmatch(address):
        raise Refused("адрес интерфейса: ожидается один IPv4-адрес с маской")
    if role is Role.EXIT_LINKS:
        if table.lower() != TABLE_OFF:
            raise Refused("в файле связи с выходом нет Table = off — маршруты этот сервис не ставит")
        if values.get("postup") or values.get("postdown"):
            raise Refused("в файле связи с выходом есть PostUp/PostDown — этот сервис их не выполняет")
    elif table:
        raise Refused("в файле есть Table, DNS, PreUp, PreDown или SaveConfig — этот сервис их не выполняет")
    if values.get("preup") or values.get("predown") or _first(values, "dns") or _first(values, "saveconfig"):
        raise Refused("в файле есть Table, DNS, PreUp, PreDown или SaveConfig — этот сервис их не выполняет")
    return InterfaceFile(
        address=address,
        mtu=int(mtu) if mtu.isdigit() else None,
        port=int(port) if port.isdigit() else None,
        private_key=SecretStr(private_key),
        postup=tuple(values.get("postup") or ()),
        setconf=SecretStr("\n".join(kept) + "\n"),
    )
