"""What the keeper answers."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Done(BaseModel):
    ok: bool = Field(default=True, description="Always true: a failure has another shape")


class Health(BaseModel):
    ok: bool = Field(default=True, description="The keeper itself answers")
    running: bool = Field(description="The resolver's process is there")
    listening: bool = Field(description="…and it takes connections where it must listen")
    wanted: bool = Field(description="False while it is stopped on request")
    started: datetime | None = Field(description="When the resolver was last started; None when never")
    starts: int = Field(ge=0, description="How many times it was started")
    exits: int = Field(ge=0, description="How many times it exited by itself")
    last_exit: int | None = Field(description="The code of its last exit; negative is a signal")


class NotDone(Exception):
    """The resolver did not do what was asked; the text is for the admin."""


class Quitting(Exception):
    """The keeper is on its way out and takes no more requests."""
