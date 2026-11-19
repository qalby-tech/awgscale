"""AdGuard Home's control API: the two calls everything else is made of."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from ..clients.tls import tls_context

DEFAULT_READ_TIMEOUT = 5.0
DEFAULT_WRITE_TIMEOUT = 10.0
SAID_LENGTH = 160


class AdGuardDown(Exception):
    """AdGuard could not be asked, or did not answer in a way that can be read."""

    def __init__(self, text: str, kind: str) -> None:
        super().__init__(text)
        self.__kind = kind

    @property
    def kind(self) -> str:
        """What sort of failure it was, as one word (it is shown in brackets after «блокировщик не отвечает»)."""
        return self.__kind

    @property
    def said(self) -> str:
        """The reason, short enough to show."""
        return str(self)[:SAID_LENGTH]


class AdGuardRefused(AdGuardDown):
    """AdGuard answered with an error of its own."""

    def __init__(self, status: int, reason: str, body: str) -> None:
        super().__init__(f"HTTP Error {status}: {reason}", "HTTPError")
        self.__status = status
        self.__body = body

    @property
    def said(self) -> str:
        """AdGuard's own words, on one line; its status when it said nothing."""
        words = " ".join(self.__body.split())
        return words[:SAID_LENGTH] or f"HTTP {self.__status}"


class AdGuardClient:
    def __init__(self, url: str, credentials_file: Path) -> None:
        self.__url = url.rstrip("/")
        self.__credentials_file = credentials_file

    def __authorization(self) -> str:
        """The admin's name and password of AdGuard, from the file its installer left ("user: …", "password: …")."""
        pairs = dict(line.strip().split(": ", 1) for line in self.__credentials_file.read_text().splitlines() if ": " in line)
        return "Basic " + base64.b64encode(f"{pairs['user']}:{pairs['password']}".encode()).decode()

    async def __ask(self, method: str, path: str, payload: object, timeout: float) -> httpx.Response:
        try:
            headers = {"Authorization": self.__authorization()}
            async with httpx.AsyncClient(timeout=timeout, verify=tls_context()) as http:
                if payload is None:
                    response = await http.request(method, f"{self.__url}/{path}", headers=headers)
                else:
                    response = await http.request(method, f"{self.__url}/{path}", headers=headers, json=payload)
        except (OSError, KeyError, ValueError, httpx.HTTPError) as e:
            raise AdGuardDown(str(e) or type(e).__name__, type(e).__name__) from None
        if response.is_error:
            raise AdGuardRefused(response.status_code, response.reason_phrase, response.text)
        return response

    async def get(self, path: str, timeout: float = DEFAULT_READ_TIMEOUT) -> Any:
        """What AdGuard answers to a question, as JSON."""
        response = await self.__ask("GET", path, None, timeout)
        try:
            return response.json()
        except ValueError as e:
            raise AdGuardDown(str(e), type(e).__name__) from None

    async def post(self, path: str, payload: object, timeout: float = DEFAULT_WRITE_TIMEOUT) -> None:
        """A change of AdGuard's settings."""
        await self.__ask("POST", path, payload, timeout)
