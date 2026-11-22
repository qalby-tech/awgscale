"""What the app gets: only what is about its own user, never the other users' addresses."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

PERCENT = 100


class ExitChoice(BaseModel):
    """Which exit the user is kept on, and why."""

    pinned: int | None = Field(default=None, description="The exit the user chose (or the admin chose for them); None is not chosen")
    group: int | None = Field(default=None, description="The exit of the user's group; None when the group keeps nobody on one")
    name: str | None = Field(default=None, description="How the exit that applies is called; None is as the shares decide")


class Member(BaseModel):
    name: str = Field(description="Name of the user")
    address: str = Field(description="Their address inside the tunnel")


class MyGroup(BaseModel):
    name: str | None = Field(default=None, description="The group's name")
    filter: str = Field(description="How its members' requests are filtered (GroupFilter)")
    space: bool = Field(description="Its members' devices reach one another")
    members: list[Member] = Field(description="Its members, with addresses — only when it is a space; otherwise empty")


class FilterChoice(BaseModel):
    """How the user's requests are filtered, and why."""

    applies: str = Field(description="The filtering that applies (GroupFilter)")
    own: str | None = Field(default=None, description="What the user chose themselves; None is as their group")
    locked: bool = Field(default=False, description="Their group is a children's one: the app does not change it")


class FilterChosen(BaseModel):
    """The answer of app-filter."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    name: str = Field(description="Whose")
    filter: FilterChoice = Field(description="How they are filtered now")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AppMe(BaseModel):
    """The answer of app-me."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    name: str = Field(description="Name of the user whose device asks")
    address: str = Field(description="Their address inside the tunnel")
    owner: bool = Field(description="They may run the network from the app")
    expires_at: datetime | None = Field(default=None, description="When their access ends; None is no term")
    exit: ExitChoice = Field(description="Which exit they are kept on")
    group: MyGroup | None = Field(default=None, description="Their group; None when they are in none")
    filter: FilterChoice = Field(description="How their requests are filtered")

    def wire(self) -> dict[str, Any]:
        shown = self.model_dump(mode="json")
        shown["expires_at"] = None if self.expires_at is None else int(self.expires_at.timestamp())
        return shown


class ExitShown(BaseModel):
    index: int = Field(description="Number of the exit")
    name: str = Field(description="How it is called")
    up: bool = Field(description="It works right now")
    share: int = Field(description="Percent of the users the shares give it")


class AppExits(BaseModel):
    """The answer of app-exits."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    exits: list[ExitShown] = Field(description="Every exit, in the settings' order")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
