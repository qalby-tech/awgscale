"""What of the users' groups reaches the routing and the blocker."""
from __future__ import annotations

from typing import Any, Sequence

from ..store.documents import Record
from .models import NO_DOH, GroupFilter, kids_lists_of

GroupSpaces = list[tuple[str, tuple[Any, ...]]]
GroupFilters = list[tuple[Any, Any, tuple[Any, ...], tuple[str, ...], bool]]


def group_spaces(records: Sequence[Record]) -> GroupSpaces:
    """What of the groups reaches the routing: the groups that are spaces."""
    return sorted((str(record.get("id")), tuple(record.get("members", []))) for record in records if record.get("space"))


def filtered_specially(record: Record) -> bool:
    """Its members are filtered otherwise than everybody: another filtering, or «Без чужого DNS»."""
    return (record.get("filter") or GroupFilter.STANDARD.value) != GroupFilter.STANDARD.value or record.get(NO_DOH) is True


def group_filters(records: Sequence[Record]) -> GroupFilters:
    """What of the groups reaches the blocker: who is filtered in a special way, with which add-on lists, and whether
    without the encrypted DNS servers."""
    return sorted(
        (
            record.get("id"),
            record.get("filter"),
            tuple(record.get("members", [])),
            kids_lists_of(record.get("kids_lists")) or (),
            record.get(NO_DOH) is True,
        )
        for record in records
        if filtered_specially(record)
    )


def without(records: Sequence[Record], gone: set[str]) -> None:
    """These users leave every record's members; the records are changed in place."""
    for record in records:
        record["members"] = [member for member in record.get("members", []) if member not in gone]


def holds_any(records: Sequence[Record], names: set[str]) -> bool:
    return any(names & set(record.get("members", [])) for record in records)
