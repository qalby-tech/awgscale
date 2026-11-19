"""How a step of a job says it did not work."""
from __future__ import annotations


class Failed(Exception):
    """A step of a job did not work. The text is shown to the admin: never a secret in it."""
