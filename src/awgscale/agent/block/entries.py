"""Names the admin types into a block group: checked and brought to one spelling."""
from __future__ import annotations

from typing import Sequence

from ..errors import Refused
from ..labels import clean_label, new_id
from ..sites.entries import checked_entries, is_network
from ..store.documents import Record
from .models import BlockGroup

MAX_GROUPS = 60
MAX_ENTRIES = 500


def clean_entries(items: object) -> list[str]:
    """The entries of one group, normalised (the same checks and words as a
    group of sites); a wrong one is refused in words."""
    try:
        return checked_entries(items, MAX_ENTRIES)
    except ValueError as e:
        raise Refused(str(e)) from None


def named(prefix: str, error: Refused) -> Refused:
    return Refused(f"{prefix}: {error.error}")


def clean_groups(raw: object, stored: Sequence[Record], kind: str) -> list[Record]:
    """The groups the page sent, checked. `stored` are the groups kept now: an id
    is kept only for a group that already exists, everything else gets a fresh
    one. Only names and patterns: a block group holds no networks."""
    if not isinstance(raw, list) or len(raw) > MAX_GROUPS:
        raise Refused(f"не больше {MAX_GROUPS} групп")
    stored_by_id = {group.get("id"): group for group in stored}
    groups: list[Record] = []
    used_ids: set[str] = set()
    group_of_entry: dict[str, str] = {}
    total = 0
    for sent in raw:
        if not isinstance(sent, dict):
            raise Refused("некорректные данные")
        name = clean_label(sent.get("name"))
        if not name:
            raise Refused("название группы: от 1 до 60 символов")
        group_id = sent.get("id")
        known = isinstance(group_id, str) and group_id in stored_by_id and group_id not in used_ids
        previous = stored_by_id.get(group_id, {}) if known else {}
        try:
            entries = clean_entries(sent.get("items", []))
        except Refused as e:
            raise named(name, e) from None
        for entry in entries:
            if entry in group_of_entry:
                raise Refused(f"запись {entry} есть в двух группах: «{group_of_entry[entry]}» и «{name}»")
            group_of_entry[entry] = name
        total += len(entries)
        if not known:
            group_id = new_id(used_ids | {str(key) for key in stored_by_id})
        used_ids.add(group_id)
        group = BlockGroup(id=group_id, name=name, on=bool(sent.get("on")), items=entries, auto=previous.get("auto"), kind=previous.get("kind") or kind)
        if any(is_network(entry) for entry in entries):
            raise Refused(f"{name}: здесь только домены и шаблоны, без сетей")
        groups.append(group.record())
    if total > MAX_ENTRIES:
        raise Refused(f"не больше {MAX_ENTRIES} записей")
    return groups
