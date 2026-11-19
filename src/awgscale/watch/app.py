"""The application of the watch service."""
from __future__ import annotations

from fastapi import FastAPI

from awgscale.service.errors import Down, Refused
from awgscale.service.http import BearerTokenMiddleware, on_down, on_refused

from .api import router
from .settings import get_settings

# no description of the API is served: this is a loopback service behind a token
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
app.add_middleware(BearerTokenMiddleware, token_file=get_settings().token_file)
app.add_exception_handler(Refused, on_refused)
app.add_exception_handler(Down, on_down)
app.include_router(router)
