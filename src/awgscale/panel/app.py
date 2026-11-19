"""The application of the panel server."""
from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException

from .deps import PanelJSON
from .routes import clients, dns, exits, invites, session, sites, state, static

ROUTING_REFUSALS = (HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED)


async def on_refusal(_: Request, refusal: Exception) -> PanelJSON:
    """Every refusal in the shape the page reads: {"ok": false, "error": …}.
    A path or a method the server does not have is the same plain 404."""
    assert isinstance(refusal, HTTPException)
    if refusal.status_code in ROUTING_REFUSALS:
        return PanelJSON({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
    return PanelJSON({"ok": False, "error": refusal.detail}, refusal.status_code)


# no description of the API is served
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, default_response_class=PanelJSON)
app.add_exception_handler(HTTPException, on_refusal)
app.include_router(session.router)
app.include_router(state.router)
app.include_router(clients.router)
app.include_router(exits.router)
app.include_router(sites.router)
app.include_router(dns.router)
app.include_router(invites.router)
app.include_router(static.router)
