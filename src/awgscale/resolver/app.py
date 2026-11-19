"""The application of the resolver's keeper."""
from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from awgscale.service.http import BearerTokenMiddleware, refusal

from .api import router
from .models import NotDone, Quitting
from .settings import get_settings


async def on_not_done(_: Request, error: Exception) -> JSONResponse:
    """The resolver did not stop or start: 500 and why, in words for the admin."""
    return refusal(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))


async def on_quitting(_: Request, error: Exception) -> JSONResponse:
    return refusal(HTTPStatus.CONFLICT, str(error))


# no description of the API is served: this is a loopback service behind a token
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
app.add_middleware(BearerTokenMiddleware, token_file=get_settings().token_file)
app.add_exception_handler(NotDone, on_not_done)
app.add_exception_handler(Quitting, on_quitting)
app.include_router(router)
