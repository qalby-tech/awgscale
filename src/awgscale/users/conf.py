"""The file the tunnel boots from: its [Interface] block is the server's own and
is carried over verbatim; the peers under it are compiled from the users."""
from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface
from pathlib import Path
from typing import Collection, Iterable, Sequence

from pydantic import SecretStr

from awgscale.core.files import PRIVATE_FILE_MODE
from awgscale.core.net import HOST_SUFFIX
from awgscale.core.patterns import KEY_RE
from awgscale.service.errors import Down

from .models import Peer

INTERFACE_HEADER = "[Interface]\n"
TAIL_BYTES = 2
PEER_HEADER = "[Peer]"
FIRST_HOST = 2                  # .0 is the network, .1 the server: the users begin at .2 of the network


def _pairs(lines: Iterable[str]) -> Iterable[tuple[str, str]]:
    for line in lines:
        if " = " in line:
            key, value = line.split(" = ", 1)
            yield key.strip(), value.strip()


@dataclass(frozen=True, slots=True)
class InterfaceBlock:
    """The [Interface] block of the tunnel's file, word for word."""

    text: str

    def values(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for key, value in _pairs(self.text.splitlines()):
            found.setdefault(key, value)
        return found

    def value(self, key: str, default: str = "") -> str:
        return self.values().get(key, default)

    def free_address(self, taken: Collection[IPv4Address]) -> IPv4Address | None:
        """The lowest address of the tunnel's network (from .2 to the one before the broadcast)
        that is neither the server's own nor a user's. A /24 gives .2 … .254, as it always did;
        a wider network (a /16, a /14) is what many users need."""
        address = self.value("Address")
        try:
            own = IPv4Interface(address)
        except ValueError:
            raise Down(f"the tunnel's address is not an IPv4 address with its mask: {address!r}") from None
        network = own.network
        if network.num_addresses < FIRST_HOST + 2:
            raise Down(f"the tunnel's network has no room for users: {address!r}")
        first = int(network.network_address) + FIRST_HOST
        last = int(network.broadcast_address) - 1
        busy = {int(address) for address in taken}
        busy.add(int(own.ip))
        for number in range(first, last + 1):
            if number not in busy:
                return IPv4Address(number)
        return None


def parse(text: str) -> tuple[InterfaceBlock, list[Peer]]:
    """→ the [Interface] block and the peers. Raises ValueError for a file this service would not have written."""
    cut = text.find("\n" + PEER_HEADER)
    block = (text if cut < 0 else text[:cut]).rstrip("\n") + "\n"
    if not block.startswith(INTERFACE_HEADER):
        raise ValueError("no [Interface] block")
    peers: list[Peer] = []
    for section in ([] if cut < 0 else text[cut + 1:].split(PEER_HEADER)[1:]):
        fields = dict(_pairs(section.splitlines()))
        address = fields.get("AllowedIPs", "").split(",")[0].strip()
        public_key = fields.get("PublicKey", "")
        preshared_key = fields.get("PresharedKey", "")
        if not (fields.get("#_Name") and KEY_RE.match(public_key) and KEY_RE.match(preshared_key) and address.endswith(HOST_SUFFIX)):
            raise ValueError("a peer this service would not have written")
        peers.append(Peer(name=fields["#_Name"], public_key=public_key, preshared_key=SecretStr(preshared_key), ip=IPv4Address(address[:-len(HOST_SUFFIX)])))
    return InterfaceBlock(block), peers


def section_of(peer: Peer) -> str:
    """One peer's block of the file."""
    return f"{PEER_HEADER}\n#_Name = {peer.name}\nPublicKey = {peer.public_key}\nPresharedKey = {peer.preshared_key.get_secret_value()}\nAllowedIPs = {peer.ip}{HOST_SUFFIX}\n"


def render(block: InterfaceBlock, peers: Sequence[Peer]) -> str:
    return block.text + "".join("\n" + section_of(peer) for peer in peers)


class TunnelFile:
    def __init__(self, path: Path) -> None:
        self.__path = path

    @property
    def path(self) -> Path:
        return self.__path

    def __read(self) -> tuple[str, InterfaceBlock, list[Peer]]:
        try:
            text = self.__path.read_text()
            block, peers = parse(text)
        except (OSError, ValueError) as e:
            raise Down(f"the tunnel's file cannot be read: {e}") from None
        return text, block, peers

    def interface(self) -> InterfaceBlock:
        """The [Interface] block: the file is read only as far as the first peer."""
        head: list[str] = []
        try:
            with open(self.__path) as file:
                for line in file:
                    if line.startswith(PEER_HEADER):
                        break
                    head.append(line)
            block, _ = parse("".join(head))
        except (OSError, ValueError) as e:
            raise Down(f"the tunnel's file cannot be read: {e}") from None
        return block

    def append(self, peer: Peer) -> None:
        """One user's block appended at the end of the file; nothing before it is written again (or rendered).
        A file that already names the key is left as it is."""
        try:
            with open(self.__path, "rb") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                file.seek(max(0, size - TAIL_BYTES))
                tail = file.read()
        except OSError as e:
            raise Down(f"the tunnel's file cannot be read: {e}") from None
        if not tail:
            raise Down("the tunnel's file is empty")
        if self.__holds(peer.public_key):
            return
        if tail.endswith(b"\n\n"):
            block = section_of(peer) + "\n"            # the blank line the installer left stays the last one
        elif tail.endswith(b"\n"):
            block = "\n" + section_of(peer)
        else:
            block = "\n\n" + section_of(peer)
        descriptor = os.open(self.__path, os.O_WRONLY | os.O_APPEND)
        os.fchmod(descriptor, PRIVATE_FILE_MODE)       # it holds the preshared keys: as private as a file written anew
        with os.fdopen(descriptor, "w") as file:
            file.write(block)           # one write of a few hundred bytes
            file.flush()
            os.fsync(file.fileno())

    def __holds(self, public_key: str) -> bool:
        try:
            with open(self.__path) as file:
                return any(line.strip() == f"PublicKey = {public_key}" for line in file)
        except OSError as e:
            raise Down(f"the tunnel's file cannot be read: {e}") from None

    def cut(self, public_key: str) -> bool:
        """The one block of this key taken out of the file; every other line stays as it is. → True when it was there."""
        try:
            text = self.__path.read_text()
        except OSError as e:
            raise Down(f"the tunnel's file cannot be read: {e}") from None
        key_line = f"\nPublicKey = {public_key}\n"
        at = text.find(key_line)
        if at < 0:
            return False
        begins = text.rfind("\n" + PEER_HEADER, 0, at)
        if begins < 0:
            raise Down(f"refusing to write {self.__path.name}: the key is not inside a [Peer] block")
        ends = text.find("\n" + PEER_HEADER, at)
        kept = text[:begins] + (text[ends:] if ends >= 0 else "\n" if text.endswith("\n\n") else "")
        if not kept.startswith(INTERFACE_HEADER):
            raise Down(f"refusing to write {self.__path.name}: no [Interface] block would be left")
        self.__replace(kept)
        return True

    def __replace(self, text: str) -> None:
        temporary = self.__path.with_name(self.__path.name + ".users.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, self.__path)

    def write(self, users: Sequence[Peer], removed: Collection[str] = ()) -> bool:
        """Compile the file from the users. `removed` are the public keys this
        change takes out of it. A render that would drop any other peer the file
        has now — a short read, or a database restored from an older copy that
        does not know somebody — is refused and the file stays as it is.
        → True when the file changed."""
        current, block, old = self.__read()
        missing = {p.public_key for p in old} - {u.public_key for u in users} - set(removed)
        if missing:
            raise Down(f"refusing to write {self.__path.name}: {len(missing)} of its {len(old)} peers are not among the {len(users)} users")
        text = render(block, users)
        if text.rstrip("\n") == current.rstrip("\n"):
            return False
        self.__replace(text)
        return True
