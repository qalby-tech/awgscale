"""What a command's answer becomes in the journal: one sentence for the admin, or nothing."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from awgscale.agent.commands import AUTO_EXIT, AUTO_FILTER, NO_TERM, Command, RouteChoice, Switch
from awgscale.agent.shares import shares_percent
from awgscale.watch.conf import RouteMode

Reply = Mapping[str, Any]
Before = Sequence[Mapping[str, Any]] | None
Sentence = tuple[str | None, bool]          # the text (None: nothing to write) and whether it is bad news

NOTHING: Sentence = (None, False)
FILTER_NAMES = {"std": "обычная", "off": "без блокировки", "kids": "детская", "pro": "Pro", "proplus": "Pro++", "ultimate": "Ultimate"}
DEFAULT_FILTER = "std"
KIDS_FILTER = "kids"
KIDS_LIST_NAMES = {"nsfw": "NSFW", "gambling": "азартные игры", "bypass": "обход фильтра", "social": "соцсети"}
SHOWN_NAMES = 4
SHOWN_USERS = 8
SHOWN_GROUP_CHANGES = 6
SHOWN_SITE_CHANGES = 5
SHELL_SHARES_COMMAND = "cascade-set"       # the shell's own (shell.ShellCommand.CASCADE_SET): described like weights-set


def names(items: Iterable[str], shown: int = SHOWN_NAMES) -> str:
    """The first few, and how many more there are."""
    listed = list(items)
    more = f" и ещё {len(listed) - shown}" if len(listed) > shown else ""
    return ", ".join(listed[:shown]) + more


def _sentence(parts: list[str], shown: int) -> Sentence:
    if not parts:
        return NOTHING
    text = "; ".join(parts[:shown]) + (f" и ещё {len(parts) - shown}" if len(parts) > shown else "")
    return text[0].upper() + text[1:], False


def _by_id(items: Before | Sequence[Mapping[str, Any]]) -> dict[Any, Mapping[str, Any]]:
    return {item.get("id"): item for item in items or []}


def _removed(reply: Reply) -> Sentence:
    gone = reply.get("removed") or []
    if not gone:
        return NOTHING
    return ("Удалён пользователь " if len(gone) == 1 else "Удалены пользователи: ") + names(gone, SHOWN_USERS), not reply.get("ok")


def _shares(reply: Reply) -> Sentence:
    if reply.get("already"):
        return NOTHING
    exits = (reply.get("cascade") or {}).get("exits", [])
    percents = shares_percent([exit_.get("weight") or 0 for exit_ in exits])
    return "Доли выходов: " + ", ".join(f"{exit_.get('name')} {percent}%" for exit_, percent in zip(exits, percents)), False


def _spaces(before: Before, reply: Reply) -> Sentence:
    old = _by_id(before)
    new = _by_id(reply.get("spaces"))
    parts = [f"создано пространство «{space.get('name')}»" for space_id, space in new.items() if space_id not in old]
    parts += [f"удалено пространство «{space.get('name')}»" for space_id, space in old.items() if space_id not in new]
    parts += [
        f"пространство «{space.get('name')}» " + ("включено" if space.get("on") else "выключено")
        for space_id, space in new.items()
        if space_id in old and bool(space.get("on")) != bool(old[space_id].get("on"))
    ]
    text = "; ".join(parts)
    return (text[0].upper() + text[1:]) if parts else "Изменены пространства", False


def _user_groups(before: Before, reply: Reply) -> Sentence:
    old = _by_id(before)
    new = _by_id(reply.get("groups"))
    was_in = {member: group_id for group_id, group in old.items() for member in group.get("members", [])}
    now_in = {member: group_id for group_id, group in new.items() for member in group.get("members", [])}
    parts = [f"создана группа «{group.get('name')}»" for group_id, group in new.items() if group_id not in old]
    parts += [f"удалена группа «{group.get('name')}»" for group_id, group in old.items() if group_id not in new]
    parts += [
        f"группа «{old[group_id].get('name')}» переименована в «{group.get('name')}»"
        for group_id, group in new.items()
        if group_id in old and group.get("name") != old[group_id].get("name")
    ]
    parts += [
        f"группа «{group.get('name')}» " + ("стала пространством" if group.get("space") else "больше не пространство")
        for group_id, group in new.items()
        if group_id in old and bool(group.get("space")) != bool(old[group_id].get("space"))
    ]
    parts += [
        f"группа «{group.get('name')}»: фильтрация — {FILTER_NAMES.get(group.get('filter') or DEFAULT_FILTER, '?')}"
        for group_id, group in new.items()
        if (group.get("filter") or DEFAULT_FILTER) != ((old.get(group_id) or {}).get("filter") or DEFAULT_FILTER)
    ]
    parts += [
        f"группа «{group.get('name')}»: списки детского режима — " + (", ".join(KIDS_LIST_NAMES.get(str(each), str(each)) for each in group.get("kids_lists") or []) or "нет")
        for group_id, group in new.items()
        if group.get("filter") == KIDS_FILTER and (old.get(group_id) or {}).get("filter") == KIDS_FILTER
        and list(group.get("kids_lists") or []) != list((old.get(group_id) or {}).get("kids_lists") or [])
    ]
    parts += [
        f"группа «{group.get('name')}»: " + ("выход по умолчанию" if group.get("exit") is None else f"закреплена за выходом {group.get('exit')}")
        for group_id, group in new.items()
        if group.get("exit") != (old.get(group_id) or {}).get("exit")
    ]
    for name in sorted(set(now_in) | set(was_in)):
        if now_in.get(name) == was_in.get(name):
            continue
        if name in now_in:
            parts.append(f"{name} → «{new[now_in[name]].get('name')}»")
        elif was_in[name] in new:
            parts.append(f"{name} убран из «{old[was_in[name]].get('name')}»")
    return _sentence(parts, SHOWN_GROUP_CHANGES)


def _site_group_changes(group: Mapping[str, Any], old: Mapping[str, Any] | None) -> list[str]:
    by_link = bool(group.get("url"))
    if old is None:
        made = f"{'подключён' if by_link else 'создана'} {'список' if by_link else 'группа сайтов'} «{group.get('name')}»"
        return [made + ("" if by_link or not group.get("items") else f" ({names(group['items'])})")]
    parts: list[str] = []
    if group.get("name") != old.get("name"):
        parts.append(f"«{old.get('name')}» переименована в «{group.get('name')}»")
    if bool(group.get("on")) != bool(old.get("on")):
        parts.append(f"«{group.get('name')}» " + ("включена" if group.get("on") else "выключена"))
    had = set(old.get("items", []))
    has = set(group.get("items", []))
    added = [item for item in group.get("items", []) if item not in had]
    removed = [item for item in old.get("items", []) if item not in has]
    if by_link:
        if added or removed:
            parts.append(f"список «{group.get('name')}» обновился: +{len(added)} −{len(removed)}")
        return parts
    if added:
        parts.append(f"в «{group.get('name')}» добавлено: {names(added)}")
    if removed:
        parts.append(f"из «{group.get('name')}» убрано: {names(removed)}")
    return parts


def _sites(before: Before, reply: Reply) -> Sentence:
    old = _by_id(before)
    new = _by_id(reply.get("groups"))
    parts: list[str] = []
    for group_id, group in new.items():
        parts += _site_group_changes(group, old.get(group_id))
    parts += [f"{'отключён список' if group.get('url') else 'удалена группа сайтов'} «{group.get('name')}»" for group_id, group in old.items() if group_id not in new]
    return _sentence(parts, SHOWN_SITE_CHANGES)


def describe(arguments: Sequence[str], before: Before, reply: Reply) -> Sentence:
    """→ (the sentence for the journal, or None when this answer is not worth one; whether it is bad news).
    `arguments` are the command as it was asked; `before` is the list it changed, as it was."""
    command = arguments[0]
    if reply.get("conflict"):
        return "Отклонено: " + str(reply.get("error", "список изменился в другом окне")), True
    if command == Command.REMOVE:
        return _removed(reply)
    if not reply.get("ok"):
        return NOTHING
    if command == Command.ADD:
        return f"Создан пользователь {arguments[1]}" + (f", срок {arguments[2]}" if len(arguments) > 2 else ""), False
    if command == Command.EXPIRE:
        users = names(arguments[2:], SHOWN_USERS)
        return (f"Срок снят: {users}" if arguments[1] == NO_TERM else f"Срок {arguments[1]}: {users}"), False
    if command == Command.SUSPEND:
        return ("Приостановлен: " if arguments[1] == Switch.ON else "Возобновлён: ") + names(arguments[2:], SHOWN_USERS), False
    if command == Command.DNS_ADD:
        return f"DNS без VPN: выдан код «{arguments[1]}»", False
    if command == Command.DNS_REMOVE:
        return f"DNS без VPN: отозван код «{arguments[1]}»", False
    if command in (Command.BLOCK_SET, Command.BLOCK_LISTS):
        return "Изменены блокировки", False
    if command == Command.EXIT_ADD:
        return f"Начато подключение нового выхода {reply.get('name')} ({reply.get('address')})", False
    if command == Command.EXIT_ATTACH:
        return NOTHING if reply.get("already") else (f"Выход {reply.get('name')} подключён к каскаду", False)
    if command == Command.EXIT_RENAME:
        return NOTHING if reply.get("already") else (f"Выход {reply.get('was')} переименован в {reply.get('name')}", False)
    if command == Command.EXIT_REMOVE:
        return f"Выход {reply.get('name')} удалён", False
    if command == Command.EXIT_UPDATE:
        return f"Начато обновление выхода {reply.get('name')}" + (" с перезагрузкой" if reply.get("reboot") else ""), False
    if command == Command.USER_EXIT:
        where = "выход по группе или авто" if arguments[1] == AUTO_EXIT else "выход " + str(reply.get("exit_name") or arguments[1])
        return f"{names(arguments[2:], SHOWN_USERS)}: {where}", False
    if command == Command.APP_FILTER:
        level = arguments[2]
        shown = "как у группы" if level == AUTO_FILTER else FILTER_NAMES.get(level, level)
        return f"{reply.get('name') or ''}: защита — {shown} (в приложении)", False
    if command == Command.APP_ROUTE:
        shown = "весь трафик через выход" if arguments[2] == RouteChoice.ALL else "умный (по режиму сети)"
        return f"{reply.get('name') or ''}: маршрут — {shown} (в приложении)", False
    if command == Command.APP_EXIT:
        where = "выход по группе или авто" if arguments[2] == AUTO_EXIT else "выход " + str(reply.get("exit_name") or arguments[2])
        return f"{names(reply.get('names') or [], SHOWN_USERS)}: {where} (в приложении)", False
    if command in (SHELL_SHARES_COMMAND, Command.WEIGHTS_SET):
        return _shares(reply)
    if command == Command.COUNTRY_SET:
        return NOTHING if reply.get("already") else ("Домашняя страна: " + arguments[1].upper(), False)
    if command == Command.MODE_SET:
        if reply.get("already"):
            return NOTHING
        return "Режим: " + ("через заграницу только по списку" if arguments[1] == RouteMode.LIST else "через заграницу всё зарубежное"), False
    if command == Command.SPACES_SET:
        return _spaces(before, reply)
    if command == Command.UGROUPS_SET:
        return _user_groups(before, reply)
    if command in (Command.SITES_SET, Command.SITES_REFRESH):
        return _sites(before, reply)
    return NOTHING
