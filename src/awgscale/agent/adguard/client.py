"""AdGuard Home's control API: the two calls everything else is made of.

The agent logs in once (control/login) and sends the session's cookie with every call. AdGuard checks the bcrypt hash
of a password with each call that carries it (HTTP Basic: about 40 ms a call on the entry), a session's cookie only
against its own table (well under a millisecond). The session lasts AdGuard's `http.session_ttl` (30 days unless
changed) and outlives a restart of AdGuard (its sessions.db); a call it no longer lets in (expired, logged out, a new
sessions.db) logs in again once and is sent again. Only an AdGuard without control/login is asked with Basic."""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx

from awgscale.core.clock import utc_now

from ..clients.tls import tls_context
from .models import Credentials, LoginRefusal

log = logging.getLogger("awgscale.agent")

DEFAULT_READ_TIMEOUT = 5.0
DEFAULT_WRITE_TIMEOUT = 10.0
SAID_LENGTH = 160
LOGIN_PATH = "login"
SESSION_COOKIE = "agh_session"
NOT_LET_IN = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})        # no session, or one AdGuard no longer has
NO_LOGIN = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED})   # an AdGuard without control/login: Basic then
# AdGuard blocks logins from an address for 15 minutes after 5 refusals (its auth_attempts, block_auth_min), Basic
# included: refused credentials are tried again only this much later, at most three times in those 15 minutes
REFUSED_PAUSE = timedelta(minutes=5)
CREDENTIALS_SEPARATOR = ": "


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


class AdGuardLoginRefused(AdGuardDown):
    """AdGuard does not let the agent log in: the admin's name or password in the file is not its own, or it blocks
    logins from the agent's address for now."""

    def __init__(self, text: str) -> None:
        super().__init__(text, "LoginRefused")


class AdGuardClient:
    def __init__(self, url: str, credentials_file: Path) -> None:
        self.__url = url.rstrip("/")
        self.__credentials_file = credentials_file
        self.__session: str | None = None           # the cookie of the agent's session; never logged
        self.__basic = False                        # AdGuard has no control/login: every call carries the password
        self.__refusal: LoginRefusal | None = None
        self.__login_lock = asyncio.Lock()          # one login at a time: the calls that wait use its session

    def __credentials(self) -> Credentials:
        """The admin's name and password of AdGuard, from the file its installer left ("user: …", "password: …")."""
        lines = self.__credentials_file.read_text().splitlines()
        pairs = dict(line.strip().split(CREDENTIALS_SEPARATOR, 1) for line in lines if CREDENTIALS_SEPARATOR in line)
        return Credentials(user=pairs["user"], password=pairs["password"])

    def __authorization(self) -> dict[str, str]:
        if self.__basic:
            credentials = self.__credentials()
            pair = f"{credentials.user}:{credentials.password.get_secret_value()}"
            return {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}
        if self.__session is not None:
            return {"Cookie": f"{SESSION_COOKIE}={self.__session}"}
        return {}           # the first call: AdGuard says whether it wants a login (one without users does not)

    async def __send(self, method: str, path: str, payload: object, timeout: float, headers: dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout, verify=tls_context()) as http:
            if payload is None:
                return await http.request(method, f"{self.__url}/{path}", headers=headers)
            return await http.request(method, f"{self.__url}/{path}", headers=headers, json=payload)

    async def __log_in(self, stale: str | None, timeout: float) -> None:
        """A new session, unless another call got one meanwhile (the one `stale` was sent with is no longer the
        agent's). Raises AdGuardLoginRefused, AdGuardRefused; OSError and the like when it cannot be asked."""
        async with self.__login_lock:
            if self.__session != stale or self.__basic:
                return
            credentials = self.__credentials()
            refusal = self.__refusal
            if refusal is not None and refusal.credentials == credentials and utc_now() < refusal.until:
                raise AdGuardLoginRefused(refusal.text)
            response = await self.__send("POST", LOGIN_PATH, credentials.login(), timeout, {})
            if response.status_code in NO_LOGIN:
                log.info("AdGuard has no control/login: its calls carry the admin's password")
                self.__basic = True
                return
            if response.status_code in NOT_LET_IN or response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                self.__refused(credentials, response)
            if response.is_error:
                raise AdGuardRefused(response.status_code, response.reason_phrase, response.text)
            session = response.cookies.get(SESSION_COOKIE)
            if not session:
                raise AdGuardDown(f"AdGuard's login answered without a session ({SESSION_COOKIE})", "NoSession")
            self.__session = session
            self.__refusal = None
            log.info("logged in to AdGuard")

    def __refused(self, credentials: Credentials, response: httpx.Response) -> None:
        words = " ".join(response.text.split())[:SAID_LENGTH]
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            text = f"AdGuard blocks logins from the agent for now: {words}"
            retry_after = response.headers.get("Retry-After", "")
            pause = timedelta(seconds=int(retry_after)) if retry_after.isdigit() else REFUSED_PAUSE
        else:
            text = f"AdGuard does not accept the admin's name and password in {self.__credentials_file}: {words}"
            pause = REFUSED_PAUSE
        self.__refusal = LoginRefusal(credentials=credentials, until=utc_now() + pause, text=text)
        log.warning("%s; not tried again for %s unless the file changes", text, pause)
        raise AdGuardLoginRefused(text)

    async def __ask(self, method: str, path: str, payload: object, timeout: float) -> httpx.Response:
        try:
            sent_with = self.__session
            response = await self.__send(method, path, payload, timeout, self.__authorization())
            if response.status_code in NOT_LET_IN and not self.__basic:
                await self.__log_in(sent_with, timeout)
                response = await self.__send(method, path, payload, timeout, self.__authorization())
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
