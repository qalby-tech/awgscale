"""The one-time move from the bash installer's files into the database.

Reads <if>.conf and the installer's per-user files, checks that this service
would produce the very same things, and only then stores the users. Prints
yes/no per check and never a key.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import SecretStr

from awgscale.core.ashell import run
from awgscale.core.patterns import KEY_RE

from .conf import InterfaceBlock, parse, render
from .device import DeviceConfigs, qr_png, uri_json
from .kernel import Kernel
from .keys import public_of
from .models import Peer, User
from .settings import UsersSettings
from .store import UserStore

# the line of a device's config with its preshared key:  PresharedKey = 8k2…=
PRESHARED_RE = re.compile(
    r"""
    ^PresharedKey\ =\ (?P<key>.+)$
    """,
    re.VERBOSE | re.MULTILINE,
)
MIN_PICTURE_LENGTH = 200


class Outcome(StrEnum):
    STORED = "stored"
    ALREADY = "already"


class Checks:
    """Named checks; each may be made many times and holds only if every time held."""

    def __init__(self) -> None:
        self.__results: dict[str, list[bool]] = {}

    def check(self, name: str, good: object) -> None:
        self.__results.setdefault(name, []).append(bool(good))

    @property
    def good(self) -> bool:
        return all(all(results) for results in self.__results.values())

    def report(self) -> None:
        for name, results in self.__results.items():
            print(f"{'yes' if all(results) else 'NO '}  {name} ({sum(results)}/{len(results)})")


def _interface_values(conf: str) -> dict[str, str]:
    return InterfaceBlock(conf.split("\n[Peer]")[0]).values()


class Importer:
    def __init__(self, settings: UsersSettings) -> None:
        self.__settings = settings
        self.__store = UserStore(settings)
        self.__kernel = Kernel(settings, run)
        self.__devices = DeviceConfigs(settings)

    def __user(self, source: Path, peer: Peer, block: InterfaceBlock, server_public: str, checks: Checks) -> User:
        conf_file = source / f"{peer.name}.conf"
        conf = conf_file.read_text()
        values = _interface_values(conf)
        private_key = values.get("PrivateKey", "")
        preshared = PRESHARED_RE.search(conf)
        term_file = source / "expiry" / peer.name
        user = User(
            name=peer.name,
            ip=peer.ip,
            public_key=peer.public_key,
            private_key=SecretStr(private_key),
            preshared_key=peer.preshared_key,
            expires_at=datetime.fromtimestamp(int(term_file.read_text().strip()), UTC) if term_file.exists() else None,
            created_at=datetime.fromtimestamp(int(conf_file.stat().st_mtime), UTC),
        )
        checks.check("each private key gives its peer's public key", KEY_RE.match(private_key) and public_of(private_key) == peer.public_key)
        checks.check(
            "each device's preshared key and address match the tunnel's",
            preshared and preshared["key"].strip() == peer.preshared_key.get_secret_value() and values.get("Address") == f"{peer.ip}/32",
        )
        mine = self.__devices.conf(user, block, server_public)
        checks.check("each device config comes out byte for byte", mine == conf)
        theirs = (source / f"{peer.name}.vpnuri").read_text().strip()
        checks.check("each vpn:// string decodes to the same content", uri_json(self.__devices.uri(user, block, server_public, mine)) == uri_json(theirs))
        checks.check("each QR picture can be made", len(qr_png(mine)) > MIN_PICTURE_LENGTH and len(qr_png(theirs)) > MIN_PICTURE_LENGTH)
        return user

    async def __store_users(self, users: list[User]) -> Outcome:
        async with self.__store.transaction() as tx:
            await tx.create_schema()
            if await tx.is_ready():
                return Outcome.ALREADY
            for user in users:
                await tx.insert(user)
            await tx.mark_ready()
        return Outcome.STORED

    async def run(self, source: Path, dry: bool = False) -> int:
        """→ the exit code: 0 when every check held (and, unless `dry`, the users are stored)."""
        current = self.__settings.conf_path.read_text()
        block, peers = parse(current)
        server_public = public_of(block.values()["PrivateKey"])
        checks = Checks()
        checks.check("the server's public key is derived correctly", (source / "server_public.key").read_text().strip() == server_public)
        users = [self.__user(source, peer, block, server_public, checks) for peer in peers]
        distinct = len({u.name for u in users}) == len({u.ip for u in users}) == len({u.public_key for u in users}) == len(users)
        checks.check("names, addresses and keys are unique", distinct)
        checks.check("the tunnel file comes out the same from the database's view", render(block, users).rstrip("\n") == current.rstrip("\n"))
        checks.check("the kernel has exactly these peers", await self.__kernel.peers() == {u.public_key for u in users})
        checks.report()
        if not checks.good:
            print("nothing was stored")
            return 1
        if dry:
            print(f"check only: {len(users)} users would be stored")
            return 0
        outcome = await self.__store_users(users)
        print(f"{outcome.value}: {len(users)} users")
        return 0
