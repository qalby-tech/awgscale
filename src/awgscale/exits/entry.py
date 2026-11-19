"""The entry's own side of a link to an exit: its key, its file, its interface."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from awgscale.core.ashell import AsyncRunner
from awgscale.core.files import PRIVATE_FILE_MODE
from awgscale.core.links import LinkPlan

from .errors import Failed
from .settings import ExitsSettings

KEY_LENGTH = 44
MIN_PARAMS = 10
PUBLIC_MARK = "PUB "
PRESHARED_MARK = "PSK "
PARAM_MARK = "PARAM "
PING_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class ExitSide:
    """What the exit told about its end of the tunnel."""

    public_key: str
    preshared_key: str
    params: tuple[str, ...]          # the masking parameters, as lines of the interface file

    @classmethod
    def from_marks(cls, marks: list[str]) -> ExitSide:
        public_key = next((mark[len(PUBLIC_MARK):].strip() for mark in marks if mark.startswith(PUBLIC_MARK)), "")
        preshared_key = next((mark[len(PRESHARED_MARK):].strip() for mark in marks if mark.startswith(PRESHARED_MARK)), "")
        params = tuple(mark[len(PARAM_MARK):] for mark in marks if mark.startswith(PARAM_MARK))
        if len(public_key) != KEY_LENGTH or len(preshared_key) != KEY_LENGTH or len(params) < MIN_PARAMS:
            raise Failed("сервер не вернул параметры туннеля")
        return cls(public_key, preshared_key, params)


def _write_private(path: Path, text: str) -> None:
    """Root-only, written beside and moved into place: nobody ever reads half a file."""
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "w") as f:
        f.write(text)
    os.replace(temporary, path)


class EntryLinks:
    def __init__(self, settings: ExitsSettings, run: AsyncRunner) -> None:
        self.__settings = settings
        self.__run = run

    async def keypair(self, plan: LinkPlan) -> tuple[str, str]:
        """The entry's key for this link, made once. → (private, public)."""
        path = self.__settings.dir / f"{plan.iface}.key"
        if not path.exists() or not path.stat().st_size:
            made = await self.__run([self.__settings.awg, "genkey"])
            if not made.ok or not made.out.strip():
                raise Failed("ключ туннеля не создан: " + made.err[:120])
            _write_private(path, made.out.strip() + "\n")
        private = path.read_text().strip()
        derived = await self.__run([self.__settings.awg, "pubkey"], private + "\n")
        if not derived.ok or len(derived.out.strip()) != KEY_LENGTH:
            raise Failed("ключ туннеля не читается")
        return private, derived.out.strip()

    def __masking_packet(self) -> str:
        """The I1 line the installer gave this server's own clients: the links use the same one."""
        try:
            for line in self.__settings.users_tunnel_file.read_text().splitlines():
                if line.startswith("I1 = "):
                    return line
        except OSError:
            pass
        return ""

    def render(self, plan: LinkPlan, address: str, private: str, side: ExitSide) -> str:
        lines = ["[Interface]", f"PrivateKey = {private}", f"Address = {plan.entry_address}/32", "MTU = 1280", "Table = off", *side.params]
        masking_packet = self.__masking_packet()
        if masking_packet:
            lines.append(masking_packet)
        lines += [
            "",
            "[Peer]",
            f"PublicKey = {side.public_key}",
            f"PresharedKey = {side.preshared_key}",
            f"Endpoint = {address}:{plan.udp_port}",
            "AllowedIPs = 0.0.0.0/0",
            "PersistentKeepalive = 25",
            "",
        ]
        return "\n".join(lines)

    def write(self, plan: LinkPlan, text: str) -> bool:
        """→ True when the file changed."""
        path = self.__settings.dir / f"{plan.iface}.conf"
        try:
            if path.read_text() == text:
                return False
        except OSError:
            pass
        _write_private(path, text)
        return True

    def has_file(self, iface: str) -> bool:
        return (self.__settings.dir / f"{iface}.conf").exists()

    async def there(self, iface: str) -> bool:
        return (await self.__run(["ip", "link", "show", iface])).ok

    async def delete(self, iface: str) -> None:
        await self.__run(["ip", "link", "del", iface])

    async def forget(self, iface: str) -> None:
        """The file, the key and the interface of a link that is no more."""
        for extension in ("conf", "key"):
            (self.__settings.dir / f"{iface}.{extension}").unlink(missing_ok=True)
        if await self.there(iface):
            await self.delete(iface)

    async def answers(self, plan: LinkPlan) -> bool:
        return (await self.__run(["ping", "-c1", "-W2", "-I", plan.iface, plan.exit_address], None, PING_TIMEOUT)).ok
