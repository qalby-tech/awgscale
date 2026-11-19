"""The application of the exits service."""
from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from awgscale.service.errors import Down, Refused
from awgscale.service.http import BearerTokenMiddleware, on_down, on_refused

from .api import router
from .settings import get_settings


async def on_invalid(_: Request, error: Exception) -> JSONResponse:
    """FastAPI's own answer to a request that does not fit its model (422) —
    without the `input` it would repeat: a request to this service may carry a root password."""
    assert isinstance(error, RequestValidationError)
    detail = [{"type": found["type"], "loc": found["loc"], "msg": found["msg"]} for found in error.errors()]
    return JSONResponse({"detail": detail}, status_code=HTTPStatus.UNPROCESSABLE_ENTITY)


# no description of the API is served: this is a loopback service behind a token
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
app.add_middleware(BearerTokenMiddleware, token_file=get_settings().token_file)
app.add_exception_handler(Refused, on_refused)
app.add_exception_handler(Down, on_down)
app.add_exception_handler(RequestValidationError, on_invalid)
app.include_router(router)
