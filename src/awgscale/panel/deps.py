"""What the routes ask for: the parts of the server, who is asking, and a body that fits its model."""
from __future__ import annotations

import json
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Any, Generic, Mapping, TypeVar

from fastapi import Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from awgscale.core.clock import utc_now

from .agent import AgentAnswer, AgentClient
from .budget import InviteBudget
from .factory import get_agent, get_invite_budget, get_login_attempts, get_password, get_prometheus, get_sessions, get_state, get_web_files
from .prometheus import Prometheus
from .sessions import LoginAttempts, Sessions
from .settings import PanelSettings, get_settings
from .state import StateBoard
from .web import WebFiles

Settings = Annotated[PanelSettings, Depends(get_settings)]
Agent = Annotated[AgentClient, Depends(get_agent)]
OpenSessions = Annotated[Sessions, Depends(get_sessions)]
Attempts = Annotated[LoginAttempts, Depends(get_login_attempts)]
Budget = Annotated[InviteBudget, Depends(get_invite_budget)]
History = Annotated[Prometheus, Depends(get_prometheus)]
State = Annotated[StateBoard, Depends(get_state)]
Files = Annotated[WebFiles, Depends(get_web_files)]
Password = Annotated[str, Depends(get_password)]
Now = Annotated[datetime, Depends(utc_now)]

DEFAULT_BODY_LIMIT = 4096


class PanelJSON(JSONResponse):
    """Every answer of the API: JSON that is never cached."""

    media_type = "application/json; charset=utf-8"

    def __init__(self, content: Any, status_code: int = HTTPStatus.OK, headers: Mapping[str, str] | None = None, **more: Any) -> None:
        super().__init__(content, status_code=status_code, headers={"Cache-Control": "no-store", **(headers or {})}, **more)


def relay(answer: AgentAnswer, refused: HTTPStatus = HTTPStatus.BAD_REQUEST) -> PanelJSON:
    """The agent's answer, passed to the page as it is; `refused` is the status when the agent said no."""
    return PanelJSON(answer, HTTPStatus.OK if answer.get("ok") else refused)


def caller_address(request: Request) -> str:
    """Who is asking. Behind Traefik the socket peer is Traefik itself, and it says
    whose request it carries; the panel's port is reached by nobody else, so the header can be trusted."""
    return request.headers.get("X-Real-Ip") or (request.client.host if request.client else "")


Caller = Annotated[str, Depends(caller_address)]


def require_session(sessions: OpenSessions, sid: Annotated[str, Cookie()] = "") -> None:
    if not sessions.is_open(sid):
        raise HTTPException(HTTPStatus.UNAUTHORIZED, "нужен вход")


def require_change(_: Annotated[None, Depends(require_session)], x_panel: Annotated[str, Header()] = "") -> None:
    """A change needs the session and a header of the page's own: a form or a script of another site cannot set it."""
    if x_panel != "1":
        raise HTTPException(HTTPStatus.BAD_REQUEST, "bad request")


LOGGED_IN = [Depends(require_session)]
MAY_CHANGE = [Depends(require_change)]

RequestT = TypeVar("RequestT", bound=BaseModel)


class Body(Generic[RequestT]):
    """A request body checked against its model. The page is the caller here and
    shows the admin what the server says, so a body that does not fit is answered
    400 with `refusal` — or with the text given for the field that is wrong."""

    def __init__(self, model: type[RequestT], refusal: str, limit: int = DEFAULT_BODY_LIMIT, by_field: Mapping[str, str] | None = None) -> None:
        self.__model = model
        self.__refusal = refusal
        self.__limit = limit
        self.__by_field = by_field or {}

    async def __within_limit(self, request: Request) -> bytes | None:
        """The body, or None when it is longer than this request may be. A body that
        says it is too long is not read at all, and one that does not say is read no further than the limit."""
        declared = request.headers.get("Content-Length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.__limit):
            return None
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > self.__limit:
                return None
        return raw

    async def __read(self, request: Request) -> dict[str, Any]:
        """The body as an object; one that is too long, is not JSON or is not an object counts as empty."""
        raw = await self.__within_limit(request)
        if raw is None:
            return {}
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def __call__(self, request: Request) -> RequestT:
        try:
            return self.__model.model_validate(await self.__read(request))
        except ValidationError as error:
            location = error.errors()[0]["loc"]
            field = str(location[0]) if location else ""
            raise HTTPException(HTTPStatus.BAD_REQUEST, self.__by_field.get(field, self.__refusal)) from None
