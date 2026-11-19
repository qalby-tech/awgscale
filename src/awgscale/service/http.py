"""What every HTTP service of the stack answers alike: who may ask, and how it says no."""
from __future__ import annotations

import hmac
import logging
from http import HTTPStatus
from pathlib import Path

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp


log = logging.getLogger("awgscale.service")

DETAIL_LENGTH = 200
BEARER = "Bearer "


def refusal(status: HTTPStatus, error: str, **more: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error, **more}, status_code=status)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Asks every caller for the token in `token_file` — before anything else is
    looked at. The file is read on each request: it can be replaced without a
    restart. An error nobody handled is answered without a traceback."""

    def __init__(self, app: ASGIApp, token_file: Path) -> None:
        super().__init__(app)
        self.__token_file = token_file

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            expected = self.__token_file.read_text().strip()
        except OSError:
            return refusal(HTTPStatus.SERVICE_UNAVAILABLE, "no token")
        given = request.headers.get("Authorization", "")
        if not expected or not hmac.compare_digest(given.encode(), (BEARER + expected).encode()):
            return refusal(HTTPStatus.UNAUTHORIZED, "unauthorized")
        try:
            return await call_next(request)
        except Exception as e:
            log.error("ERROR %s", repr(e)[:300])
            return refusal(HTTPStatus.INTERNAL_SERVER_ERROR, "внутренняя ошибка")


async def on_refused(_: Request, error: Exception) -> JSONResponse:
    """The service turned the request down: 400 and why, in words for the admin."""
    return refusal(HTTPStatus.BAD_REQUEST, str(error))


async def on_down(_: Request, error: Exception) -> JSONResponse:
    """Something the service depends on is away: 503, never an empty answer."""
    log.info("unavailable: %s", error)
    return refusal(HTTPStatus.SERVICE_UNAVAILABLE, "сервис сейчас не может выполнить запрос", detail=str(error)[:DETAIL_LENGTH])
