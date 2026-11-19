"""What a command is handed as base64 of JSON: opened, and nothing in it believed yet."""
from __future__ import annotations

import base64
import json
from typing import Any

from .errors import Refused


def decode(payload: str) -> dict[str, Any]:
    """The JSON object inside. Anything else is refused."""
    try:
        data = json.loads(base64.b64decode(payload, validate=True))
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise Refused("некорректные данные")
    return data
