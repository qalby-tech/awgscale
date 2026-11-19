"""The day's tops, as they are counted and as they are kept between looks."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

TOP_HOURS = 24
TOP_LENGTH = 10


class Count(BaseModel):
    """A site or service and how many times it was looked up. By name, never by who asked."""

    model_config = ConfigDict(frozen=True)

    name: Any = Field(description="The service, or the site's own domain")
    count: Any = Field(description="Lookups in the period")


def counts_of(held: object) -> tuple[Count, ...]:
    if not isinstance(held, list):
        return ()
    return tuple(Count(name=entry.get("name"), count=entry.get("count")) for entry in held if isinstance(entry, dict))


def top(counted: Mapping[str, int]) -> tuple[Count, ...]:
    """The most looked-up, largest first; equal counts by name."""
    ordered = sorted(counted.items(), key=lambda pair: (-pair[1], pair[0]))
    return tuple(Count(name=name, count=count) for name, count in ordered[:TOP_LENGTH])


class Tops(BaseModel):
    """What the users looked up in the last day, and what the block lists stopped."""

    model_config = ConfigDict(frozen=True)

    at: datetime | None = Field(default=None, description="When it was counted; None for an answer that was not")
    hours: Any = Field(default=TOP_HOURS, description="The period, in hours")
    total: Any = Field(default=0, description="Lookups of everything that is a site")
    sites: tuple[Count, ...] = Field(default=(), description="The most visited overall")
    blocked_lists: dict[str, Any] | None = Field(default=None, description="Requests each block list stopped: {list id as text: count}; None in a file of an older shape")
    exit: tuple[Count, ...] | None = Field(default=None, description="The most visited of what goes through the exits; None in a file of an older shape")
    exit_total: Any = Field(default=0, description="Lookups of what goes through the exits")
    direct: tuple[Count, ...] = Field(default=(), description="The most visited of what leaves directly")
    direct_total: Any = Field(default=0, description="Lookups of what leaves directly")

    @classmethod
    def of(cls, record: Mapping[str, Any]) -> Tops:
        """The kept file, read the forgiving way it always was."""
        at = record.get("at")
        blocked = record.get("blocked_lists")
        return cls(
            at=datetime.fromtimestamp(at, UTC) if type(at) in (int, float) else None,
            hours=record.get("hours", TOP_HOURS),
            total=record.get("total", 0),
            sites=counts_of(record.get("sites")),
            blocked_lists=dict(blocked) if isinstance(blocked, dict) else ({} if "blocked_lists" in record else None),
            exit=counts_of(record.get("exit")) if "exit" in record else None,
            exit_total=record.get("exit_total", 0),
            direct=counts_of(record.get("direct")),
            direct_total=record.get("direct_total", 0),
        )

    @property
    def whole(self) -> bool:
        """Has it everything a count of today's shape has."""
        return self.exit is not None and self.blocked_lists is not None

    def record(self) -> dict[str, Any]:
        """As it is kept in the file between looks: the moment as Unix seconds."""
        assert self.at is not None
        return {
            "at": int(self.at.timestamp()),
            "hours": self.hours,
            "total": self.total,
            "sites": [count.model_dump() for count in self.sites],
            "blocked_lists": dict(self.blocked_lists or {}),
            "exit": [count.model_dump() for count in self.exit or ()],
            "exit_total": self.exit_total,
            "direct": [count.model_dump() for count in self.direct],
            "direct_total": self.direct_total,
        }

    def wire(self) -> dict[str, Any]:
        """How the tops appear inside `state.dns`: the overall list and the two
        halves — what goes through the foreign exits and what leaves directly."""
        return {
            "top_sites": [count.model_dump() for count in self.sites],
            "top_sites_total": self.total,
            "top_exit": [count.model_dump() for count in self.exit or ()],
            "top_exit_total": self.exit_total,
            "top_direct": [count.model_dump() for count in self.direct],
            "top_direct_total": self.direct_total,
            "top_sites_hours": self.hours,
        }
