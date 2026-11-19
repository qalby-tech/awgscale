"""The exits service proper: the requests, and the one job that may run at a time."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from pydantic import SecretStr

from awgscale.core.clock import utc_now
from awgscale.core.links import LinkPlan
from awgscale.service.errors import Refused

from .entry import EntryLinks
from .errors import Failed
from .models import (
    AddRequest, Adopted, AdoptRequest, Job, JobKind, JobState, Node, NodesAnswer, Removed, RemoveRequest, Started, Step, StepKey, StepState, UpdateRequest,
)
from .registry import Registry
from .settings import ExitsSettings
from .ssh import Ssh
from .steps import PLANS, JobSteps, Work

log = logging.getLogger("awgscale.exits")

Clock = Callable[[], datetime]
ERROR_LENGTH = 200


class ExitsService:
    def __init__(self, settings: ExitsSettings, registry: Registry, ssh: Ssh, entry: EntryLinks, steps: JobSteps, clock: Clock = utc_now) -> None:
        self.__settings = settings
        self.__registry = registry
        self.__ssh = ssh
        self.__entry = entry
        self.__steps = steps
        self.__clock = clock
        self.__busy = asyncio.Lock()           # held while a job runs
        self.__current: Job | None = None
        self.__running: asyncio.Task[None] | None = None

    # ── the jobs ─────────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self.__busy.locked()

    async def job(self) -> Job | None:
        """The job running now, or the last one that ran."""
        return self.__current or await self.__registry.last_job()

    async def job_finished(self) -> None:
        """Returns when no job runs any more."""
        if self.__running is not None:
            await asyncio.wait([self.__running])

    async def __start(self, kind: JobKind, index: int, node: Node, secret: SecretStr | None = None, **fields: Any) -> None:
        if self.__busy.locked():
            raise Refused("другая операция с выходами ещё идёт — дождитесь её окончания")
        await self.__busy.acquire()
        try:
            started = self.__clock()
            job = Job(
                id=f"{int(started.timestamp())}-{index}",
                kind=kind,
                index=index,
                address=node.address,
                started=started,
                steps=[Step(key=plan.key, title=plan.title) for plan in PLANS[kind]],
                **fields,
            )
            await self.__registry.save_job(job)
        except BaseException:
            self.__busy.release()
            raise
        self.__current = job
        self.__running = asyncio.create_task(self.__run_job(Work(job, node, secret)))

    async def __run_job(self, work: Work) -> None:
        job = work.job
        try:
            log.info("job %s for exit %s (%s) started", job.kind.value, job.index, job.address)
            for step in job.steps:
                step.state = StepState.RUN
                await self.__registry.save_job(job)
                try:
                    result = await self.__steps.run(step.key, work)
                except Failed as e:
                    await self.__fail(job, step, f"{step.title}: {e}", f"FAILED at {step.key.value}: {e}")
                    return
                except Exception as e:
                    await self.__fail(job, step, f"{step.title}: внутренняя ошибка", f"ERROR at {step.key.value}: {type(e).__name__}: {str(e)[:ERROR_LENGTH]}")
                    return
                step.state = result or StepState.DONE
                if step.key is StepKey.LOGIN:
                    work.secret = None                 # used: not kept a step longer
            job.state = JobState.DONE
            job.finished = self.__clock()
            await self.__registry.save_job(job)
            if job.kind in (JobKind.ADD, JobKind.UPDATE):
                await self.__registry.set_node(job.index, updated=self.__clock())
            log.info("job %s for exit %s done", job.kind.value, job.index)
        except Exception as e:                         # the record could not be kept: the job is over all the same
            log.error("job %s for exit %s lost: %s: %s", job.kind.value, job.index, type(e).__name__, str(e)[:ERROR_LENGTH])
        finally:
            self.__busy.release()

    async def __fail(self, job: Job, step: Step, error: str, line: str) -> None:
        step.state = StepState.FAIL
        job.state = JobState.FAILED
        job.error = error
        job.finished = self.__clock()
        await self.__registry.save_job(job)
        log.info("job %s for exit %s %s", job.kind.value, job.index, line)

    async def mark_interrupted(self) -> None:
        """At start: a job cut short by a restart is said to be so; starting it again goes on from where it was."""
        last = await self.__registry.last_job()
        if last and last.state is JobState.RUNNING:
            last.state = JobState.FAILED
            last.finished = self.__clock()
            last.error = "операция прервана перезапуском сервиса — запустите её ещё раз"
            await self.__registry.save_job(last)

    async def prepare(self) -> None:
        """At start: the service's own key is made if it is not there, and a job cut short is said to be so."""
        await self.__ssh.public_key()
        await self.mark_interrupted()

    # ── the requests ─────────────────────────────────────────────────────────

    def __other_server(self, request: AddRequest | AdoptRequest) -> None:
        """Refuses an address that cannot be an exit of this entry."""
        if not request.address.is_global or str(request.address) == self.__settings.entry_addr:
            raise Refused("адрес сервера: нужен внешний адрес другого сервера")

    async def add(self, request: AddRequest) -> Started:
        self.__other_server(request)
        nodes = await self.__registry.nodes()
        for index, node in nodes.items():
            if node.address == request.address and index != request.index:
                raise Refused(f"сервер {request.address} уже записан как выход {index}")
        mine = nodes.get(request.index)
        if mine and mine.address != request.address:
            raise Refused(f"номер {request.index} занят другим сервером")
        if mine and not mine.managed:
            raise Refused("этот выход настроен не этим сервисом — им он не управляет")
        node = Node(address=request.address, ssh_port=request.ssh_port, added=mine.added if mine else None)
        await self.__start(JobKind.ADD, request.index, node, request.password, name=request.name, weight=request.weight)
        return Started()

    async def update(self, request: UpdateRequest) -> Started:
        node = await self.__registry.node(request.index)
        if not node:
            raise Refused("такого выхода в записях нет")
        if not node.managed:
            raise Refused("этот выход настроен вручную — сервис на него не заходит. Обновите его сами или подключите заново через панель")
        await self.__start(JobKind.UPDATE, request.index, node, name=request.name, reboot=request.reboot)
        return Started()

    async def remove(self, request: RemoveRequest) -> Removed:
        """The entry's side goes at once (the caller has taken the exit out of the cascade already);
        what is on the server itself is cleaned afterwards, if we may log in."""
        if self.busy:
            raise Refused("другая операция с выходами ещё идёт — дождитесь её окончания")
        node = await self.__registry.node(request.index)
        await self.__entry.forget(request.iface or LinkPlan(request.index).iface)
        await self.__registry.drop_node(request.index)
        cleaning = False
        if node and node.managed:
            try:
                await self.__start(JobKind.CLEAN, request.index, node, name=request.name)
                cleaning = True
            except Refused:
                pass
        log.info("exit %s removed from the entry%s", request.index, ", cleaning the server" if cleaning else "")
        return Removed(cleaning=cleaning)

    async def adopt(self, request: AdoptRequest) -> Adopted:
        """Write down a link that exists already (made before this service, or by hand)."""
        self.__other_server(request)
        plan = LinkPlan(request.index)
        iface = request.iface or plan.iface
        if not self.__entry.has_file(iface):
            raise Refused("файла этой связи на сервере нет")
        if request.managed and iface != plan.iface:
            raise Refused("управлять можно только выходом, у которого номер совпадает с номером интерфейса")
        await self.__registry.set_node(request.index, iface=iface, address=request.address, ssh_port=request.ssh_port, managed=request.managed, port=plan.udp_port)
        return Adopted()

    async def nodes(self) -> NodesAnswer:
        """Everything the panel shows about the exits as servers: who they are, the job, the key to put on a server by hand."""
        job = await self.job()
        return NodesAnswer(
            nodes={str(index): node for index, node in (await self.__registry.nodes()).items()},
            job=job,
            busy=self.busy,
            key=await self.__ssh.authorized_line(),
        )
