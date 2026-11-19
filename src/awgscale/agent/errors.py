"""The ways a command of the agent says no."""
from __future__ import annotations

from typing import Any


class Refused(Exception):
    """An answer for whoever asked: {"ok": false, "error": …}, with whatever else the page needs beside it."""

    def __init__(self, error: str, **beside: Any) -> None:
        super().__init__(error)
        self.__beside = beside

    @property
    def error(self) -> str:
        return str(self)

    @property
    def beside(self) -> dict[str, Any]:
        """What the answer carries beside the reason: a conflict mark, the list as it is now."""
        return dict(self.__beside)


class StoreDown(Exception):
    """The settings could not be read or written. Not the same as "no settings"."""


class StoreRefused(ValueError):
    """A document the store does not take (a record without its own id, a pause that is not name → time)."""
