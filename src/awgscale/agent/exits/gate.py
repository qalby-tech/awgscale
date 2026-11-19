"""The exits service as the commands of this package ask it."""
from __future__ import annotations

from pydantic import SecretStr

from ..clients.exits import AddBody, ExitsClient, Registry, RemoveBody, UpdateBody
from ..clients.http import ServiceDown, ServiceRefused
from ..errors import Refused
from ..ports import Outcome

REGISTRY_TIMEOUT = 30.0
JOB_TIMEOUT = 40.0


def _away(error: ServiceDown) -> str:
    """What to say of a service that gave no answer one could use."""
    return "сервис выходов сейчас не может выполнить запрос" if error.status else "сервис выходов не отвечает"


class ExitsService:
    """The exits service behind its client."""

    def __init__(self, client: ExitsClient) -> None:
        self.__client = client

    async def registry(self) -> Registry:
        try:
            return await self.__client.registry(REGISTRY_TIMEOUT)
        except ServiceRefused as e:
            raise Refused(str(e)) from None
        except ServiceDown as e:
            raise Refused(_away(e)) from None

    async def add(self, index: int, name: str, weight: int, address: str, ssh_port: int, password: SecretStr | None) -> Outcome:
        body = AddBody(index=index, name=name, weight=weight, address=address, ssh_port=ssh_port, password=password)
        try:
            await self.__client.add(body, JOB_TIMEOUT)
        except ServiceRefused as e:
            return Outcome(False, str(e))
        except ServiceDown as e:
            return Outcome(False, _away(e))
        return Outcome(True)

    async def remove(self, index: int, iface: str, name: str) -> Outcome:
        try:
            await self.__client.remove(RemoveBody(index=index, iface=iface, name=name), JOB_TIMEOUT)
        except ServiceRefused as e:
            return Outcome(False, str(e))
        except ServiceDown as e:
            return Outcome(False, _away(e))
        return Outcome(True)

    async def update(self, index: int, reboot: bool, name: str) -> Outcome:
        try:
            await self.__client.update(UpdateBody(index=index, reboot=reboot, name=name), JOB_TIMEOUT)
        except ServiceRefused as e:
            return Outcome(False, str(e))
        except ServiceDown as e:
            return Outcome(False, _away(e))
        return Outcome(True)
