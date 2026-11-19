"""The steps jobs are made of. Every step can be run again: a job started anew goes on from where the last one stopped."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable, Mapping

from pydantic import SecretStr

from awgscale.agent.commands import Command
from awgscale.core.links import LinkPlan

from .agent import Agent
from .entry import EntryLinks, ExitSide
from .errors import Failed
from .models import Job, JobKind, Node, StepKey, StepState
from .registry import Registry
from .settings import ExitsSettings
from .ssh import Ssh, Target

OK_FACTS_MARK = "OK "
REBOOT_MARK = "REBOOT"
IMPATIENT_SECONDS = 0.3                # how long a wait lasts when the settings say not to wait (the tests)
LINK_APPEARS_SECONDS = 40.0
TUNNEL_ANSWERS_SECONDS = 40.0
EXIT_UP_SECONDS = 150.0
SERVER_RETURNS_SECONDS = 420.0
GOING_DOWN_SECONDS = 20.0              # after a reboot is asked for: a login that still works is the old boot
PREPARE_TIMEOUT = 1500.0
LINK_TIMEOUT = 180.0
ARM_TIMEOUT = 90.0
KEEP_TIMEOUT = 60.0
STATS_TIMEOUT = 600.0
PACKAGES_TIMEOUT = 2400.0
CLEAN_TIMEOUT = 120.0
REBOOT_COMMAND_TIMEOUT = 30.0
STATE_TIMEOUT = 40.0
EXIT_UP = "up"


@dataclass(slots=True)
class Work:
    """A job in progress: the record the panel sees, and what the steps need but the record must not hold."""

    job: Job
    node: Node
    secret: SecretStr | None = None

    @property
    def plan(self) -> LinkPlan:
        return LinkPlan(self.job.index)

    @property
    def target(self) -> Target:
        return Target(str(self.node.address), self.node.ssh_port)


StepRun = Callable[[Work], Awaitable[StepState | None]]


@dataclass(frozen=True, slots=True)
class StepPlan:
    key: StepKey
    title: str


PLANS: Mapping[JobKind, tuple[StepPlan, ...]] = {
    JobKind.ADD: (
        StepPlan(StepKey.LOGIN, "Вход на сервер"),
        StepPlan(StepKey.PREPARE, "Проверка и установка пакетов"),
        StepPlan(StepKey.LINK, "Туннель"),
        StepPlan(StepKey.TUNNEL, "Проверка туннеля"),
        StepPlan(StepKey.FIREWALL, "Файрвол"),
        StepPlan(StepKey.STATS, "Мониторинг"),
        StepPlan(StepKey.ATTACH, "Подключение к каскаду"),
        StepPlan(StepKey.UP, "Проверка выхода"),
    ),
    JobKind.UPDATE: (
        StepPlan(StepKey.LOGIN, "Вход на сервер"),
        StepPlan(StepKey.PACKAGES, "Обновление пакетов"),
        StepPlan(StepKey.PREPARE, "Модуль туннеля"),
        StepPlan(StepKey.REBOOT, "Перезагрузка"),
        StepPlan(StepKey.FIREWALL, "Файрвол"),
        StepPlan(StepKey.STATS, "Мониторинг"),
        StepPlan(StepKey.TUNNEL, "Проверка туннеля"),
    ),
    JobKind.CLEAN: (
        StepPlan(StepKey.LOGIN, "Вход на сервер"),
        StepPlan(StepKey.CLEAN, "Уборка на сервере"),
    ),
}


class JobSteps:
    def __init__(self, settings: ExitsSettings, ssh: Ssh, entry: EntryLinks, registry: Registry, agent: Agent) -> None:
        self.__settings = settings
        self.__ssh = ssh
        self.__entry = entry
        self.__registry = registry
        self.__agent = agent
        self.__runs: Mapping[StepKey, StepRun] = {
            StepKey.LOGIN: self.login,
            StepKey.PREPARE: self.prepare,
            StepKey.LINK: self.link,
            StepKey.TUNNEL: self.tunnel,
            StepKey.FIREWALL: self.firewall,
            StepKey.STATS: self.stats,
            StepKey.ATTACH: self.attach,
            StepKey.UP: self.up,
            StepKey.PACKAGES: self.packages,
            StepKey.REBOOT: self.reboot,
            StepKey.CLEAN: self.clean,
        }

    async def run(self, key: StepKey, work: Work) -> StepState | None:
        """One step of a job. → how it ended when that is not plainly "done"; raises Failed with words for the admin."""
        return await self.__runs[key](work)

    @property
    def __patient(self) -> bool:
        return self.__settings.wait > 0

    async def __wait_for(self, check: Callable[[], Awaitable[bool]], seconds: float) -> bool:
        """Asks until the answer is yes, for at most that long. → was it yes."""
        end = time.monotonic() + (seconds if self.__patient else IMPATIENT_SECONDS)
        while True:
            if await check():
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(self.__settings.wait)

    # ── on the server ────────────────────────────────────────────────────────

    async def login(self, work: Work) -> None:
        if work.secret:
            await self.__ssh.install_key(work.target, work.secret)
        if not await self.__ssh.can_login(work.target, strict=False):
            raise Failed("вход по ключу не удался" + ("" if work.secret else ": пароль не указан, а ключа этого сервера на нём нет"))

    async def prepare(self, work: Work) -> None:
        await self.__ssh.send_file(work.target, self.__settings.ppa_key, "/etc/apt/keyrings/amnezia-ppa.gpg")
        marks = await self.__ssh.script(work.target, "prepare.sh", timeout=PREPARE_TIMEOUT)
        work.job.facts = next((mark[len(OK_FACTS_MARK):] for mark in marks if mark.startswith(OK_FACTS_MARK)), "")

    async def link(self, work: Work) -> None:
        plan = work.plan
        private, public = await self.__entry.keypair(plan)
        marks = await self.__ssh.script(work.target, "link.sh", env={"ENTRY_PUB": public, "PORT": plan.udp_port, "NET": plan.net}, timeout=LINK_TIMEOUT)
        changed = self.__entry.write(plan, self.__entry.render(plan, str(work.node.address), private, ExitSide.from_marks(marks)))
        await self.__registry.set_node(
            plan.index,
            address=work.node.address,
            ssh_port=work.node.ssh_port,
            managed=True,
            port=plan.udp_port,
            added=work.node.added or datetime.now(UTC),
        )
        if changed and await self.__entry.there(plan.iface):
            await self.__entry.delete(plan.iface)          # an old link with other settings: the links service makes the new one

        async def link_is_there() -> bool:
            return await self.__entry.there(plan.iface)

        if not await self.__wait_for(link_is_there, LINK_APPEARS_SECONDS):
            raise Failed(f"интерфейс {plan.iface} не появился — сервис связей (vpn-links) не работает?")

    async def tunnel(self, work: Work) -> None:
        async def tunnel_answers() -> bool:
            return await self.__entry.answers(work.plan)

        if not await self.__wait_for(tunnel_answers, TUNNEL_ANSWERS_SECONDS):
            raise Failed("туннель до сервера не отвечает: возможно, его порт закрыт у провайдера или адрес недоступен со входного сервера")

    async def firewall(self, work: Work) -> None:
        plan = work.plan
        settings = self.__settings
        await self.__ssh.send_file(work.target, settings.remote / "exit-firewall.sh", "/usr/local/sbin/exit-firewall.sh", "755")
        stats_rule = f"-i awgc -p tcp -s {plan.entry_address} -d {plan.exit_address} --dport {settings.stats_port}"
        await self.__ssh.script(
            work.target, "firewall.sh", args="arm", timeout=ARM_TIMEOUT,
            env={"SSH_PORT": work.node.ssh_port, "UDP_PORT": work.node.port or plan.udp_port, "STATS_RULE": stats_rule},
        )
        # a NEW login: the one that armed the rules would pass as "established"
        if not await self.__ssh.can_login(work.target):
            raise Failed("после включения файрвола сервер не пускает по SSH — правила сами снимутся через две минуты")
        await self.__ssh.script(work.target, "firewall.sh", args="keep", timeout=KEEP_TIMEOUT)

    async def stats(self, work: Work) -> None:
        for name in ("server.py", "Dockerfile"):
            await self.__ssh.send_file(work.target, self.__settings.remote / "stats" / name, f"/opt/vpn-exit-stats/{name}")
        await self.__ssh.script(work.target, "stats.sh", env={"ADDR": work.plan.exit_address, "PORT": self.__settings.stats_port}, timeout=STATS_TIMEOUT)

    async def packages(self, work: Work) -> None:
        marks = await self.__ssh.script(work.target, "update.sh", timeout=PACKAGES_TIMEOUT)
        work.job.facts = next((mark[len(OK_FACTS_MARK):] for mark in marks if mark.startswith(OK_FACTS_MARK)), "")
        work.job.reboot_needed = any(mark.startswith(REBOOT_MARK) for mark in marks)

    async def reboot(self, work: Work) -> StepState | None:
        if not work.job.reboot:
            return StepState.SKIP
        await self.__ssh.command(work.target, "(sleep 1; systemctl reboot) >/dev/null 2>&1 &", timeout=REBOOT_COMMAND_TIMEOUT)
        await asyncio.sleep(GOING_DOWN_SECONDS if self.__patient else 0)

        async def server_is_back() -> bool:
            return await self.__ssh.can_login(work.target)

        if not await self.__wait_for(server_is_back, SERVER_RETURNS_SECONDS):
            raise Failed("сервер не вернулся после перезагрузки за семь минут")
        answer = await self.__ssh.command(work.target, "lsmod | grep -c '^amneziawg'; systemctl is-active awg-quick@awgc")
        if answer.out.split() != ["1", "active"]:
            raise Failed("после перезагрузки туннель на сервере не поднялся сам")
        work.job.reboot_needed = False
        return None

    async def clean(self, work: Work) -> None:
        await self.__ssh.script(work.target, "clean.sh", timeout=CLEAN_TIMEOUT)

    # ── in the cascade ───────────────────────────────────────────────────────

    async def attach(self, work: Work) -> None:
        document = {"index": work.job.index, "name": work.job.name, "weight": work.job.weight or 1}
        answer = await self.__agent([Command.EXIT_ATTACH, base64.b64encode(json.dumps(document).encode()).decode()])
        if not answer.get("ok"):
            raise Failed("выход не добавлен в настройки каскада: " + str(answer.get("error", ""))[:200])

    async def __exit_state(self, index: int) -> str | None:
        for found in (await self.__agent([Command.STATE], timeout=STATE_TIMEOUT)).get("exits") or []:
            if found.get("index") == index:
                state: str | None = found.get("state")
                return state
        return None

    async def up(self, work: Work) -> None:
        async def exit_is_up() -> bool:
            return await self.__exit_state(work.job.index) == EXIT_UP

        if not await self.__wait_for(exit_is_up, EXIT_UP_SECONDS):
            raise Failed("выход добавлен, но сторож маршрутов пока не считает его рабочим — пользователи на него не идут; посмотрите его состояние через несколько минут")
