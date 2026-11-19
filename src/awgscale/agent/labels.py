"""Names the admin types and ids the agent hands out."""
from __future__ import annotations

import re
import secrets
from typing import Collection

# The id of a stored group or space:   g00001   a1b2c3
ID_RE = re.compile(
    r"""
    ^[a-z0-9]{6}\Z
    """,
    re.VERBOSE,
)
MAX_LABEL_LENGTH = 60
ID_BYTES = 3
FIRST_PRINTABLE = 32
DELETE = 127


def clean_label(text: object, limit: int = MAX_LABEL_LENGTH) -> str | None:
    """A name typed by the admin, with its spaces put in order; None for what is
    not a name. It is only ever stored and shown, never used in a command;
    control characters are refused all the same."""
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.split())
    if not cleaned or len(cleaned) > limit or any(ord(char) < FIRST_PRINTABLE or ord(char) == DELETE for char in cleaned):
        return None
    return cleaned


def new_id(taken: Collection[str]) -> str:
    """An id nobody has yet: six hexadecimal characters."""
    while True:
        candidate = secrets.token_hex(ID_BYTES)
        if candidate not in taken:
            return candidate
