"""DNS over HTTPS (RFC 8484): /dns-query, or /dns-query/<personal code>."""
from __future__ import annotations

import base64
import struct
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .admission import Admission
from .factory import get_admission, get_gate
from .service import Gate
from .wire import ANSWER_TTL, MAX_MESSAGE_BYTES

DNS_MESSAGE = "application/dns-message"


def plain(status: HTTPStatus, text: str) -> Response:
    """A short answer in words; the content type exactly as devices have always got it."""
    return Response(content=text, status_code=status, headers={"Content-Type": "text/plain"})


# Everything a request passes through is a coroutine: it runs on the event loop,
# never in a worker thread — the admissions are changed from one place only.
async def the_admission() -> Admission:
    return get_admission()


async def the_gate() -> Gate:
    return get_gate()


async def admitted(request: Request, admission: Annotated[Admission, Depends(the_admission)]) -> None:
    """Lets the request through only with a valid code in its address, or from an address that needs none. Anyone else: 404."""
    # Traefik is the only way in (loopback port), so its header is trustworthy
    address = (request.headers.get("X-Real-Ip") or (request.client.host if request.client else "")).strip()
    if not admission.admit(address, request.path_params.get("code")):
        raise HTTPException(HTTPStatus.NOT_FOUND)


Service = Annotated[Gate, Depends(the_gate)]

router = APIRouter(dependencies=[Depends(admitted)])


async def answered(gate: Gate, message: bytes) -> Response:
    if not message or len(message) > MAX_MESSAGE_BYTES:
        return plain(HTTPStatus.BAD_REQUEST, "bad dns message")
    try:
        reply = await gate.resolve(message)
    except (OSError, struct.error):
        return plain(HTTPStatus.BAD_GATEWAY, "upstream resolver failed")
    return Response(content=reply, headers={"Content-Type": DNS_MESSAGE, "Cache-Control": f"max-age={ANSWER_TTL}"})


@router.get("/dns-query")
@router.get("/dns-query/")
@router.get("/dns-query/{code}")
@router.get("/dns-query/{code}/")
async def query_in_address(gate: Service, dns: str = "") -> Response:
    if not dns:
        return plain(HTTPStatus.BAD_REQUEST, "missing dns parameter")
    try:
        message = base64.urlsafe_b64decode(dns + "=" * (-len(dns) % 4))
    except ValueError:
        return plain(HTTPStatus.BAD_REQUEST, "bad dns parameter")
    return await answered(gate, message)


@router.post("/dns-query")
@router.post("/dns-query/")
@router.post("/dns-query/{code}")
@router.post("/dns-query/{code}/")
async def query_in_body(request: Request, gate: Service) -> Response:
    try:
        length = int(request.headers.get("Content-Length", "0"))
    except ValueError:
        length = 0
    if not 0 < length <= MAX_MESSAGE_BYTES:
        return plain(HTTPStatus.BAD_REQUEST, "bad length")
    return await answered(gate, await request.body())
