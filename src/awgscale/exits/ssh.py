"""Talking to an exit server: logins, our scripts, files. One connection per call, never multiplexed."""
from __future__ import annotations

import asyncio
import base64
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from pydantic import SecretStr

from awgscale.core.ashell import TIMED_OUT, AsyncRunner
from awgscale.core.shell import Result

from .errors import Failed
from .models import DEFAULT_SSH_PORT
from .settings import ExitsSettings

ASKPASS = Path(__file__).parent / "askpass.sh"
OK_MARK = "@@OK"
MARK = "@@"
ERROR_MARK = "ERR "
SSH_UNREACHABLE = 255                  # ssh's own exit code when it could not connect or log in
LOGIN_TIMEOUT = 40.0
PASSWORD_LOGIN_TIMEOUT = 60.0
FILE_TIMEOUT = 120.0
SCRIPT_TIMEOUT = 900.0


@dataclass(frozen=True, slots=True)
class Target:
    address: str
    ssh_port: int = DEFAULT_SSH_PORT


class Ssh:
    def __init__(self, settings: ExitsSettings, run: AsyncRunner) -> None:
        self.__settings = settings
        self.__run = run

    # ── the service's own key ────────────────────────────────────────────────

    async def public_key(self) -> str:
        """Made at the first start. → the public line."""
        key = self.__settings.key_file
        if not key.exists():
            key.parent.mkdir(parents=True, exist_ok=True)
            made = await self.__run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "vpn-entry-exits", "-f", str(key)])
            if not made.ok:
                raise Failed("ключ SSH не создан: " + made.err[:120])
        return key.with_name(key.name + ".pub").read_text().strip()

    async def authorized_line(self) -> str:
        """The line for an exit's authorized_keys: accepted only from the entry's address."""
        restriction = f'from="{self.__settings.entry_addr}" ' if self.__settings.entry_addr else ""
        return restriction + await self.public_key()

    # ── calls ────────────────────────────────────────────────────────────────

    def __base(self, target: Target, strict: bool = True) -> list[str]:
        settings = self.__settings
        return [
            settings.ssh,
            "-i", str(settings.key_file),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", f"UserKnownHostsFile={settings.known_hosts}",
            "-o", f"StrictHostKeyChecking={'yes' if strict else 'accept-new'}",
            "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4",
            "-o", "ControlMaster=no",
            "-o", "LogLevel=ERROR",
            "-p", str(target.ssh_port),
            f"root@{target.address}",
        ]

    async def command(self, target: Target, command: str, timeout: float = LOGIN_TIMEOUT) -> Result:
        return await self.__run([*self.__base(target), command], None, timeout)

    async def can_login(self, target: Target, strict: bool = True) -> bool:
        answer = await self.__run([*self.__base(target, strict), f"echo {OK_MARK}"], None, LOGIN_TIMEOUT)
        return answer.ok and OK_MARK in answer.out

    async def script(self, target: Target, name: str, env: Mapping[str, object] | None = None, args: str = "", timeout: float = SCRIPT_TIMEOUT) -> list[str]:
        """Run one of our scripts on the exit (bash -s over SSH). → the lines it printed with "@@".
        Raises Failed with what the script said (its "@@ERR" line), never with its whole output."""
        body = (self.__settings.remote / name).read_text()
        prefix = " ".join(f"{key}={shlex.quote(str(value))}" for key, value in (env or {}).items())
        answer = await self.__run([*self.__base(target), f"{prefix} bash -s -- {args}".strip()], body, timeout)
        marks = [line[len(MARK):].strip() for line in answer.out.splitlines() if line.startswith(MARK)]
        said = [mark[len(ERROR_MARK):] for mark in marks if mark.startswith(ERROR_MARK)]
        if said:
            raise Failed(said[0][:300])
        if not answer.ok:
            why = "сервер не отвечает по SSH" if answer.code == SSH_UNREACHABLE else "время вышло" if answer.code == TIMED_OUT else f"код {answer.code}"
            detail = f" ({answer.err.splitlines()[-1][:120]})" if answer.err and answer.code != SSH_UNREACHABLE else ""
            raise Failed(f"{name}: {why}{detail}")
        return marks

    async def send_file(self, target: Target, local: Path, path: str, mode: str = "644") -> None:
        data = base64.b64encode(local.read_bytes()).decode()
        quoted = shlex.quote(path)
        command = f"mkdir -p $(dirname {quoted}) && base64 -d > {quoted}.new && chmod {mode} {quoted}.new && mv -f {quoted}.new {quoted}"
        answer = await self.__run([*self.__base(target), command], data, FILE_TIMEOUT)
        if not answer.ok:
            raise Failed(f"файл {os.path.basename(path)} не доставлен на сервер")

    async def install_key(self, target: Target, password: SecretStr) -> None:
        """Log in once with the password and put the key there. The password lives in this
        process's memory and in the environment of one ssh child, nowhere else."""
        line = shlex.quote(await self.authorized_line())
        settings = self.__settings
        command = [
            settings.ssh,
            "-o", f"UserKnownHostsFile={settings.known_hosts}",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            "-o", "LogLevel=ERROR",
            "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "PubkeyAuthentication=no",
            "-o", "NumberOfPasswordPrompts=1",
            "-o", "ControlMaster=no",
            "-p", str(target.ssh_port),
            f"root@{target.address}",
            f"umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF {line} ~/.ssh/authorized_keys || echo {line} >> ~/.ssh/authorized_keys; echo {OK_MARK}",
        ]
        # the one child that is handed the password: through its environment, where ssh's askpass helper reads it
        environment = dict(os.environ, SSH_ASKPASS=str(ASKPASS), SSH_ASKPASS_REQUIRE="force", EXITS_PW=password.get_secret_value(), DISPLAY=":0")
        try:
            child = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
                start_new_session=True,
            )
        except OSError:
            raise Failed("ssh не запустился") from None
        try:
            out, _ = await asyncio.wait_for(child.communicate(), PASSWORD_LOGIN_TIMEOUT)
        except TimeoutError:
            child.kill()
            await child.wait()
            raise Failed("сервер не ответил на вход по паролю") from None
        if OK_MARK not in out.decode(errors="replace"):
            raise Failed("вход по паролю не удался: проверьте адрес, пароль и что на сервере разрешён вход root по паролю")
