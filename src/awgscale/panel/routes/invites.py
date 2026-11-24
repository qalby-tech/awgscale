"""Invitations: the admin makes and revokes the links; a person with a link opens
its page, names a device and gets a user of their own.

The pages under /i/ are the only part of this server strangers reach: Traefik
lets the whole internet to /i/<secret> and to nothing else. The page is static;
the secret is checked by the agent. Here: the path must be exactly that shape,
every address gets a small budget of requests, and a wrong secret costs more of
it than a right one.
"""
from __future__ import annotations

import asyncio
import re
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from awgscale.agent.commands import Command
from awgscale.service.qr import qr_png

from ..agent import AgentAnswer
from ..arguments import packed
from ..budget import Cost
from ..deps import LOGGED_IN, MAY_CHANGE, Agent, Body, Budget, Caller, PanelJSON, Settings, relay
from ..models import InviteCodeRequest, InviteRequest, RedeemRequest

router = APIRouter()

# The address of an invitation:  /i/Zx3k…(22 characters)   and of what is known about it:  /i/Zx3k…/info
INVITE_RE = re.compile(
    r"""
    ^(?P<secret>[A-Za-z0-9_-]{22})
    (?P<info>/info)?
    /?\Z
    """,
    re.VERBOSE,
)
# The link of an invitation as the page shows it:   https://vpn.example.org/i/Zx3k…(22 characters)
INVITE_LINK_RE = re.compile(
    r"""
    ^https://
    (?P<host>[a-z0-9.-]{1,253}(?::[0-9]{1,5})?)
    /i/
    (?P<secret>[A-Za-z0-9_-]{22})
    \Z
    """,
    re.VERBOSE | re.IGNORECASE,
)
# The number of an invitation:   abc123
INVITE_ID_RE = re.compile(
    r"""
    [a-z0-9]{6}
    """,
    re.VERBOSE,
)
INVITE_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# nothing about an invitation is kept by a browser, sent on to another site, or indexed
PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}
SHOWN_ABOUT = ("ok", "group", "left", "total", "expires_at")
SHOWN_AFTER_REDEEM = ("ok", "name", "uri", "uri_png", "conf", "conf_png", "left", "group", "owner")
BODY_LIMIT = 300


def refused(status: HTTPStatus, error: str) -> PanelJSON:
    return PanelJSON({"ok": False, "error": error}, status, headers=PRIVATE)


# ── the admin's side ─────────────────────────────────────────────────────────

@router.get("/api/invites", dependencies=LOGGED_IN)
async def invitations(agent: Agent) -> AgentAnswer:
    return await agent.call(Command.INVITE_LIST, timeout=30)


@router.post("/api/invites", dependencies=MAY_CHANGE)
async def invite(request: Annotated[InviteRequest, Depends(Body(InviteRequest, "некорректные данные", limit=BODY_LIMIT))], agent: Agent) -> PanelJSON:
    """A new invitation; the answer carries the link's secret, shown once."""
    return relay(await agent.call(Command.INVITE_ADD, packed(request.model_dump()), timeout=30))


@router.post("/api/invites/qr", dependencies=MAY_CHANGE)
async def invite_code(request: Annotated[InviteCodeRequest, Depends(Body(InviteCodeRequest, "некорректные данные", limit=BODY_LIMIT + 200))]) -> PanelJSON:
    """The link just made as a QR code: a phone's camera (the awgscale app's, or the system's) reads it off the screen."""
    if not INVITE_LINK_RE.match(request.link):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "некорректные данные")
    return PanelJSON({"ok": True, "png": await asyncio.to_thread(qr_png, request.link)}, headers=PRIVATE)


@router.delete("/api/invites/{invite_id:path}", dependencies=MAY_CHANGE)
async def revoke(invite_id: str, agent: Agent) -> PanelJSON:
    if not INVITE_ID_RE.fullmatch(invite_id):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "некорректные данные")
    return relay(await agent.call(Command.INVITE_REMOVE, invite_id, timeout=30))


# ── the side of a person with a link ─────────────────────────────────────────

def invite_page(settings: Settings) -> Response:
    """The page itself: the same for any secret."""
    try:
        page = settings.invite_file.read_bytes()
    except OSError:
        return refused(HTTPStatus.NOT_FOUND, "not found")
    headers = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": INVITE_CSP, **PRIVATE}
    if settings.secure:
        headers["Strict-Transport-Security"] = "max-age=15552000"
    return Response(page, media_type="text/html; charset=utf-8", headers=headers)


async def invite_info(secret: str, caller: str, agent: Agent, budget: Budget) -> PanelJSON:
    answer = await agent.call(Command.INVITE_INFO, secret, timeout=20)
    if not answer.get("ok"):
        budget.allows(caller, Cost.WRONG_SECRET)              # a wrong secret is dear
        return refused(HTTPStatus.NOT_FOUND, "Ссылка недействительна или уже использована")
    return PanelJSON({key: answer.get(key) for key in SHOWN_ABOUT}, headers=PRIVATE)


@router.get("/i/{rest:path}")
async def open_invitation(rest: str, caller: Caller, agent: Agent, budget: Budget, settings: Settings) -> Response:
    address = INVITE_RE.match(rest)
    if address is None:
        return refused(HTTPStatus.NOT_FOUND, "not found")
    if not budget.allows(caller, Cost.PAGE):
        return refused(HTTPStatus.TOO_MANY_REQUESTS, "слишком много запросов")
    if address["info"]:
        return await invite_info(address["secret"], caller, agent, budget)
    return invite_page(settings)


def shown_error(answer: dict[str, Any]) -> str:
    """Only what the agent wrote for the person with the link is passed on; anything else
    (a crash, a timeout, the daemon's own words) becomes one fixed sentence."""
    if answer.get("shown") is True and isinstance(answer.get("error"), str):
        return answer["error"]
    return "Не получилось создать подключение — попробуйте ещё раз чуть позже"


@router.post("/i/{rest:path}")
async def redeem(rest: str, request: Request, caller: Caller, agent: Agent, budget: Budget, x_invite: Annotated[str, Header()] = "") -> PanelJSON:
    """A person with a link names a device and gets a user of their own."""
    address = INVITE_RE.match(rest)
    if address is None or address["info"]:
        return refused(HTTPStatus.NOT_FOUND, "not found")
    if x_invite != "1" or not budget.allows(caller, Cost.REDEEM):
        return refused(HTTPStatus.TOO_MANY_REQUESTS, "слишком много запросов")
    try:
        device = await Body(RedeemRequest, "Имя: латинские буквы, цифры, _ и -, до 32 символов", limit=BODY_LIMIT)(request)
    except HTTPException as bad_name:
        return refused(HTTPStatus.BAD_REQUEST, bad_name.detail)
    answer = await agent.call(Command.INVITE_REDEEM, address["secret"], device.name, timeout=420)
    if not answer.get("ok"):
        budget.allows(caller, Cost.REDEEM_FAILED)
        return refused(HTTPStatus.BAD_REQUEST, shown_error(answer))
    return PanelJSON({key: answer[key] for key in SHOWN_AFTER_REDEEM if key in answer}, headers=PRIVATE)
