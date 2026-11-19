"""One call to a service of the stack on loopback: bearer token from a file, JSON both ways."""
from __future__ import annotations

from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .tls import tls_context

ERROR_LENGTH = 160               # what is kept of an error's text


class Method(StrEnum):
    GET = "GET"
    POST = "POST"
    DELETE = "DELETE"


class ServiceDown(Exception):
    """The service could not be asked, or could not do its work right now. Never
    the same as an empty answer. `status` is set when it did answer, with an error of its own."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.__status = status

    @property
    def status(self) -> int | None:
        return self.__status


class ServiceRefused(Exception):
    """The service turned the request down; the text is its own, in words for the admin."""


class Refusal(BaseModel):
    """How a service of the stack says no (400)."""

    ok: bool = Field(default=False, description="Always false")
    error: str = Field(default="", description="Why, in words for the admin")


class ValidationProblem(BaseModel):
    """One entry of FastAPI's answer to a request that does not fit its model (422)."""

    msg: str = Field(default="", description="What is wrong with the field")


class Unprocessable(BaseModel):
    detail: list[ValidationProblem] = Field(default_factory=list, description="What does not fit, field by field")

    def text(self) -> str:
        """The first problem, without pydantic's own prefix."""
        first = self.detail[0].msg if self.detail else ""
        return first.removeprefix("Value error, ")


class ServiceClient:
    """Asks one service. A refusal (400, or a body that did not fit, 422) is
    ServiceRefused; everything else that is not an answer is ServiceDown."""

    def __init__(self, base_url: str, token_file: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.__base_url = base_url.rstrip("/")
        self.__token_file = token_file
        self.__transport = transport

    async def ask(self, method: Method, path: str, body: BaseModel | None = None, timeout: float = 30) -> Any:
        """→ the service's answer as parsed JSON."""
        try:
            token = self.__token_file.read_text().strip()
        except OSError as e:
            raise ServiceDown(str(e)[:ERROR_LENGTH]) from None
        content = None if body is None else body.model_dump_json(exclude_none=True).encode()
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(base_url=self.__base_url, transport=self.__transport, timeout=timeout, verify=tls_context()) as client:
                response = await client.request(method.value, path, content=content, headers=headers)
        except httpx.HTTPError as e:
            raise ServiceDown(str(e)[:ERROR_LENGTH] or type(e).__name__) from None
        try:
            if response.status_code == HTTPStatus.BAD_REQUEST:
                raise ServiceRefused(Refusal.model_validate(response.json()).error or "ошибка")
            if response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
                raise ServiceRefused(Unprocessable.model_validate(response.json()).text() or "ошибка")
            if response.status_code != HTTPStatus.OK:
                raise ServiceDown(self.__reason(response), response.status_code)
            return response.json()
        except ValueError as e:
            raise ServiceDown(str(e)[:ERROR_LENGTH]) from None

    @staticmethod
    def __reason(response: httpx.Response) -> str:
        """Why the service could not do it, in its own words when it gave any."""
        try:
            return Refusal.model_validate(response.json()).error or f"HTTP {response.status_code}"
        except ValueError:
            return f"HTTP {response.status_code}"
