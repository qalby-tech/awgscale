"""The page itself and the files it loads. Like the page they hold nothing secret, so no login is asked."""
from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..deps import Files, Settings
from ..web import ASSETS_PREFIX, IMMUTABLE_PREFIX

router = APIRouter()

# Nothing is loaded from other sites, and nothing else may be either.
PAGE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


@router.get("/")
async def page(files: Files, settings: Settings) -> Response:
    index = files.index()
    if index is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "not found")
    headers = {
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": PAGE_CSP,
    }
    if settings.secure:
        headers["Strict-Transport-Security"] = "max-age=15552000"
    return Response(index, media_type="text/html; charset=utf-8", headers=headers)


@router.get("/_next/{rest:path}")
async def asset(rest: str, files: Files) -> Response:
    """The scripts and styles of the page."""
    path = ASSETS_PREFIX + rest
    found = files.asset(path)
    if found is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "not found")
    content, content_type = found
    caching = "public, max-age=31536000, immutable" if path.startswith(IMMUTABLE_PREFIX) else "no-store"
    return Response(content, headers={"Content-Type": content_type, "Cache-Control": caching, "X-Content-Type-Options": "nosniff"})


@router.get("/fonts/{name:path}")
async def font(name: str, files: Files) -> Response:
    """The page's own typefaces."""
    content = files.font(name)
    if content is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "not found")
    return Response(content, media_type="font/woff2", headers={"Cache-Control": "public, max-age=2592000", "X-Content-Type-Options": "nosniff"})
