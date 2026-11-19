"""The groups of sites taken together: what they add up to, their fingerprint,
and the check of what the page sends. Nothing here touches the world."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Collection, Sequence

from ..labels import clean_label
from ..users.models import version_of
from .entries import checked_entries, is_network
from .lists import link_of
from .models import MAX_ENTRIES, MAX_GROUPS, MAX_SITE_LISTS, SiteGroup

AUTOMATIC_TELEGRAM = "telegram"
EDITED_OF_A_LIST = ("id", "name", "on", "url")

NewId = Callable[[Collection[str]], str]


def flat_entries(groups: Sequence[SiteGroup]) -> list[str]:
    """The flat list the routing uses: the entries of the groups that are on."""
    entries: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not group.on:
            continue
        for entry in group.items:
            if entry not in seen:
                seen.add(entry)
                entries.append(entry)
    return entries


def typed_entries(groups: Sequence[SiteGroup]) -> list[str]:
    """The same, of the groups the admin typed: a list by link is thousands of names."""
    return flat_entries([group for group in groups if not group.by_link])


def automatic_on(groups: Sequence[SiteGroup]) -> bool:
    """Is the group whose networks update by themselves switched on."""
    return any(group.auto == AUTOMATIC_TELEGRAM and group.on for group in groups)


def version_of_sites(groups: Sequence[SiteGroup]) -> str:
    """The fingerprint of what the admin edits on the sites page. The entries of
    a list by link are left out: they are refreshed nightly by themselves, which
    is not somebody's change."""
    edited: list[dict[str, Any]] = []
    for group in groups:
        record = group.wire()
        edited.append({key: record.get(key) for key in EDITED_OF_A_LIST} if group.by_link else record)
    return version_of(edited)


@dataclass(frozen=True, slots=True)
class CheckedGroups:
    """The groups the page sent, checked."""

    groups: list[SiteGroup]
    new_lists: frozenset[str]           # ids of the lists by link that are still to be downloaded


def checked_groups(raw: object, old: Sequence[SiteGroup], new_id: NewId, kinds: Sequence[str] | None = None, lists: bool = False) -> CheckedGroups:
    """Checks the groups sent by the panel. `old` are the stored groups: ids are
    kept only for groups that already exist, everything else gets a fresh one.
    With `lists`, a group may be a list by link: of a stored one only the name
    and the switch are taken from the panel (its entries are the file behind the
    link, not something typed); a new one arrives with `url` and is named in
    `new_lists` for the caller to download. Raises ValueError with the reason."""
    if not isinstance(raw, list) or len(raw) > MAX_GROUPS:
        raise ValueError(f"не больше {MAX_GROUPS} групп")
    stored = {group.id: group for group in old}
    groups: list[SiteGroup] = []
    ids: set[str] = set()
    owner_of: dict[str, str] = {}
    links: set[str] = set()
    new_lists: set[str] = set()
    typed = 0
    for sent in raw:
        if not isinstance(sent, dict):
            raise ValueError("некорректные данные")
        name = clean_label(sent.get("name"))
        if not name:
            raise ValueError("название группы: от 1 до 60 символов")
        group_id = sent.get("id")
        previous = stored.get(group_id) if isinstance(group_id, str) and group_id not in ids else None
        known = previous is not None
        switched_on = bool(sent.get("on"))
        if lists and ((previous is not None and previous.by_link) or (not known and sent.get("url") is not None)):
            if previous is not None:
                listed = SiteGroup(
                    id=previous.id, name=name, on=switched_on, url=previous.url, items=previous.items,
                    updated=previous.updated, error=previous.error, skipped=previous.skipped,
                )
            else:
                link = link_of(sent.get("url"))
                if link is None:
                    raise ValueError(f"{name}: нужна ссылка на файл списка, начинается с https://")
                listed = SiteGroup(id=new_id(ids | set(stored)), name=name, on=switched_on, url=link.url, items=(), updated=None, error="", skipped=0)
                new_lists.add(listed.id)
            if listed.url in links:
                raise ValueError(f"{name}: этот список уже подключён")
            assert listed.url is not None
            links.add(listed.url)
            ids.add(listed.id)
            groups.append(listed)
            continue
        try:
            entries = checked_entries(sent.get("items", []), MAX_ENTRIES)
        except ValueError as e:
            raise ValueError(f"{name}: {e}") from None
        for entry in entries:
            if entry in owner_of:
                raise ValueError(f"запись {entry} есть в двух группах: «{owner_of[entry]}» и «{name}»")
            owner_of[entry] = name
        typed += len(entries)
        if not known:
            group_id = new_id(ids | set(stored))
        assert isinstance(group_id, str)
        ids.add(group_id)
        fields: dict[str, Any] = {"id": group_id, "name": name, "on": switched_on, "items": tuple(entries)}
        if previous is not None and previous.auto:
            fields["auto"] = previous.auto
        if kinds:
            sent_kind = sent.get("kind")
            fields["kind"] = (previous.kind if previous is not None else None) or (sent_kind if sent_kind in kinds else kinds[0])
            if any(is_network(entry) for entry in entries):
                raise ValueError(f"{name}: здесь только домены и шаблоны, без сетей")
        groups.append(SiteGroup(**fields))
    if typed > MAX_ENTRIES:
        raise ValueError(f"не больше {MAX_ENTRIES} записей")
    if len(links) > MAX_SITE_LISTS:
        raise ValueError(f"не больше {MAX_SITE_LISTS} списков по ссылке")
    return CheckedGroups(groups=groups, new_lists=frozenset(new_lists))
