"""The two ways a service says no."""
from __future__ import annotations


class Refused(Exception):
    """An answer for the caller: a bad name, a taken name, no such thing."""


class Down(Exception):
    """Something the service depends on could not be asked. Never treated as an empty answer."""
