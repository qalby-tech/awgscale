"""What every command of this package gets on its way through the dispatcher:
its answer turned into the line the socket sends, its refusals put into words,
and — for a command that changes something — a sentence in the journal.

A handler takes its request and returns a typed answer, or raises Refused. It
never formats an error and never writes to the journal itself.
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Awaitable, Callable, Generic, Protocol, TypeVar

from pydantic import SecretStr

from .commands import CommandRequest
from .errors import Refused, StoreDown, StoreRefused
from .journal.models import Kind, Who
from .journal.service import Journal
from .journal.texts import Before, describe
from .protocol import Caller, Reply

log = logging.getLogger("awgscale.agent")

DETAIL_LENGTH = 200


class Answer(Protocol):
    """What a handler returns: something that knows its shape on the wire."""

    def wire(self) -> dict[str, Any]: ...


Request = TypeVar("Request", bound=CommandRequest)
Work = Callable[[Request], Awaitable[Answer]]
SecretWork = Callable[[Request, SecretStr | None], Awaitable[Answer]]      # a handler that is also handed what came beside the arguments
Snapshot = Callable[[], Awaitable[Before]]


def reply_of_refusal(refusal: Refused) -> Reply:
    return {"ok": False, "error": refusal.error, **refusal.beside}


async def answered(work: Awaitable[Answer]) -> Reply:
    """A handler's work → the line the socket sends. Never raises."""
    try:
        return (await work).wire()
    except Refused as e:
        return reply_of_refusal(e)
    except StoreDown as e:
        return {"ok": False, "error": "Хранилище настроек недоступно", "store": str(e)[:DETAIL_LENGTH]}
    except StoreRefused as e:
        return {"ok": False, "error": "Не сохранено: настройки не того вида", "store": str(e)[:DETAIL_LENGTH]}
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": "ошибка агента: " + repr(e)[:DETAIL_LENGTH]}


class Handled(Generic[Request]):
    """A handler as the dispatcher runs it."""

    def __init__(self, work: Work[Request] | SecretWork[Request], journal: Journal | None = None, before: Snapshot | None = None, with_secret: bool = False) -> None:
        self.__work = work
        self.__with_secret = with_secret    # the handler takes the secret that came beside the arguments (it is never journaled)
        self.__journal = journal            # given for a command that changes something
        self.__before = before              # what was stored before the change, when the sentence needs it

    async def __snapshot(self) -> Before:
        if self.__before is None:
            return None
        try:
            return await self.__before()
        except Exception:
            return None                     # the journal never stands between a command and its answer

    async def __note(self, request: CommandRequest, before: Before, reply: Reply, caller: Caller) -> None:
        if self.__journal is None:
            return
        try:
            text, bad = describe(request.to_args(), before, reply)
            if not text:
                return
            kind = Kind.REFUSED if reply.get("conflict") else Kind.CHANGE
            who = Who(caller.value) if caller.value else Who.SYSTEM
            await self.__journal.write(text, bad, kind, who)
        except Exception as e:
            log.info("journal not written: %s", repr(e)[:DETAIL_LENGTH])

    async def __call__(self, request: CommandRequest, caller: Caller, secret: SecretStr | None) -> Reply:
        before = await self.__snapshot() if self.__journal is not None else None
        work = self.__work(request, secret) if self.__with_secret else self.__work(request)      # type: ignore[arg-type, call-arg]
        reply = await answered(work)
        await self.__note(request, before, reply, caller)
        return reply
