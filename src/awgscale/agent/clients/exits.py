"""The exits service (awgscale.exits), asked through its own models."""
from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_serializer

from awgscale.exits.models import Job, Node

from .http import Method, ServiceClient


class Registry(BaseModel):
    """Everything the panel shows about the exits as servers."""

    nodes: dict[str, Node] = Field(default_factory=dict, description="The servers, by the exit's number")
    job: Job | None = Field(default=None, description="The job that runs, or the last one")
    busy: bool = Field(default=False, description="A job is running")
    key: str = Field(default="", description="The line to put on a server by hand, when no password is given")


class AddBody(BaseModel):
    index: int = Field(description="Number of the new exit")
    name: str = Field(description="Its name")
    weight: int = Field(description="The percent of the users it takes")
    address: str = Field(description="The server's public address")
    ssh_port: int = Field(description="Where its SSH listens")
    password: SecretStr | None = Field(default=None, description="Root's password, used once to put the key in; never logged or stored")

    @field_serializer("password", when_used="json")
    def reveal_password(self, password: SecretStr | None) -> str | None:
        """The one place the password leaves this process: the request to the exits service."""
        return None if password is None else password.get_secret_value()


class RemoveBody(BaseModel):
    index: int = Field(description="Number of the exit")
    iface: str | None = Field(default=None, description="Its interface on the entry")
    name: str = Field(default="", description="Its name")


class UpdateBody(BaseModel):
    index: int = Field(description="Number of the exit")
    reboot: bool = Field(default=False, description="Restart the server after the update")
    name: str = Field(default="", description="Its name")


class ExitsClient:
    def __init__(self, client: ServiceClient) -> None:
        self.__client = client

    async def registry(self, timeout: float = 30) -> Registry:
        return Registry.model_validate(await self.__client.ask(Method.GET, "/v1/nodes", timeout=timeout))

    async def add(self, body: AddBody, timeout: float = 30) -> None:
        """Begins the job that makes the server an exit; it is followed through the registry."""
        await self.__client.ask(Method.POST, "/v1/add", body, timeout=timeout)

    async def remove(self, body: RemoveBody, timeout: float = 40) -> None:
        await self.__client.ask(Method.POST, "/v1/remove", body, timeout=timeout)

    async def update(self, body: UpdateBody, timeout: float = 40) -> None:
        await self.__client.ask(Method.POST, "/v1/update", body, timeout=timeout)
