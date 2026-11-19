"""What goes to the agent as arguments of a command: checked names, and structured data packed into one safe word."""
from __future__ import annotations

import base64
import json
from http import HTTPStatus
from typing import Any

from fastapi import HTTPException

from awgscale.core.patterns import NAME_RE


def packed(payload: dict[str, Any]) -> str:
    """A structured argument for the agent: JSON, in base64 so it is one safe word of a command."""
    return base64.b64encode(json.dumps(payload).encode()).decode()


def checked_name(name: str) -> str:
    """A name taken from the address of a request, refused before it can become an argument."""
    if not NAME_RE.match(name):
        raise HTTPException(HTTPStatus.BAD_REQUEST, "некорректное имя")
    return name
