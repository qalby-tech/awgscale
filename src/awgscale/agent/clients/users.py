"""The users service (awgscale.users), asked through its own models."""
from __future__ import annotations

from pydantic import BaseModel, Field

from awgscale.users.models import Created, CreatedAnswer, DevicePayload, RemovalsAnswer, UserView, UsersAnswer

from .http import Method, ServiceClient, ServiceRefused


class AddBody(BaseModel):
    name: str = Field(description="Name of the new user")
    expires: str | None = Field(default=None, description='Term counted from now, like "7d"; None is no term')


class NamesBody(BaseModel):
    names: list[str] = Field(description="The users this is about")


class ExpiryBody(BaseModel):
    names: list[str] = Field(description="Whose term changes")
    expires: str | None = Field(default=None, description="The new term counted from now; None is no term")


class UsersClient:
    def __init__(self, client: ServiceClient) -> None:
        self.__client = client

    async def list(self) -> list[UserView]:
        return UsersAnswer.model_validate(await self.__client.ask(Method.GET, "/v1/users")).users

    async def add(self, name: str, expires: str | None) -> Created:
        answer = await self.__client.ask(Method.POST, "/v1/users", AddBody(name=name, expires=expires), timeout=60)
        return CreatedAnswer.model_validate(answer).results[0]

    async def remove(self, names: list[str]) -> RemovalsAnswer:
        return RemovalsAnswer.model_validate(await self.__client.ask(Method.DELETE, "/v1/users", NamesBody(names=names), timeout=120))

    async def set_expiry(self, names: list[str], expires: str | None) -> None:
        await self.__client.ask(Method.POST, "/v1/users/expiry", ExpiryBody(names=names, expires=expires))

    async def config(self, name: str) -> DevicePayload | None:
        """What the device needs to connect; None when there is no such user."""
        try:
            return DevicePayload.model_validate(await self.__client.ask(Method.GET, f"/v1/users/{name}/config"))
        except ServiceRefused:
            return None
