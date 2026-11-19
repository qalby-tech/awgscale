"""The application of the DNS gate."""
from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Request, Response

from .api import plain, router


async def on_not_found(_: Request, __: Exception) -> Response:
    """An unknown path and a request that is not admitted look the same from outside."""
    return plain(HTTPStatus.NOT_FOUND, "not found")


# no description of the API is served: the address of a device's resolver is all there is to it
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
app.add_exception_handler(HTTPStatus.NOT_FOUND, on_not_found)
app.include_router(router)
