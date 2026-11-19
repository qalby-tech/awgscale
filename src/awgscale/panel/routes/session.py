"""Logging in and out."""
from __future__ import annotations

import hmac
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException

from ..deps import Attempts, Body, Caller, OpenSessions, PanelJSON, Password, Settings
from ..models import LoginRequest

router = APIRouter(prefix="/api")


@router.post("/login")
async def log_in(
    request: Annotated[LoginRequest, Depends(Body(LoginRequest, "неверный пароль"))],
    caller: Caller, attempts: Attempts, sessions: OpenSessions, password: Password, settings: Settings,
) -> PanelJSON:
    if attempts.must_wait(caller):
        raise HTTPException(HTTPStatus.TOO_MANY_REQUESTS, "слишком много попыток, подождите 5 минут")
    if not hmac.compare_digest(str(request.password).encode(), password.encode()):
        attempts.failed(caller)
        raise HTTPException(HTTPStatus.FORBIDDEN, "неверный пароль")
    lifetime = int(settings.session_ttl.total_seconds())
    cookie = f"sid={sessions.open()}; HttpOnly; SameSite=Strict; Path=/; Max-Age={lifetime}"
    if settings.secure:
        cookie += "; Secure"
    return PanelJSON({"ok": True}, headers={"Set-Cookie": cookie})


@router.post("/logout")
async def log_out(sessions: OpenSessions, sid: Annotated[str, Cookie()] = "") -> PanelJSON:
    sessions.close(sid)
    return PanelJSON({"ok": True}, headers={"Set-Cookie": "sid=; Path=/; Max-Age=0"})
