"""What a look at the state adds to what was seen last. Pure: who calls decides about locking and writing."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from awgscale.watch.health import ExitState

from ..journal.models import Kind
from ..spans import ago
from ..users.models import KnownUser
from .models import Event, ExitNote, ExitSeen, Notes

TERM_SLACK = timedelta(seconds=900)       # the users service removes once in five minutes; a term that ends this soon has run out
KNOWN_STATES = (ExitState.UP, ExitState.DOWN)


def gone_event(name: str, expires: datetime | None, now: datetime) -> Event:
    if expires is not None and expires <= now + TERM_SLACK:
        return Event(text=f"Срок истёк: {name} удалён", kind=Kind.TERM)
    return Event(text=f"Пользователь {name} удалён не через панель", kind=Kind.TERM)


def turned_event(exit_seen: ExitSeen, was: ExitNote, now: datetime) -> Event:
    if exit_seen.state is ExitState.UP:
        silent = f" (не отвечал {ago(now - was.since)})" if was.since is not None else ""
        return Event(text=f"Выход {exit_seen.name} снова работает" + silent, kind=Kind.EXIT)
    return Event(text=f"Выход {exit_seen.name} недоступен", bad=True, kind=Kind.EXIT)


def next_notes(seen: Notes, users: Sequence[KnownUser], exits: Sequence[ExitSeen], now: datetime) -> tuple[Notes, list[Event]]:
    """→ (what to store, the events) from what was stored and what a look sees."""
    leaving = {name: until for name, until in seen.leaving.items() if until > now}
    events: list[Event] = []
    clients = {user.name: user.expires_at for user in users if user.name}
    if not clients and seen.clients and not all(name in leaving for name in seen.clients):
        clients = dict(seen.clients)                         # an empty list of users is a failed read, not "everyone left"
    else:
        for name in leaving:                                 # being removed by the agent: not ours to watch any more
            clients.pop(name, None)
        for name, expires in (seen.clients or {}).items():
            if name not in clients and name not in leaving:
                events.append(gone_event(name, expires, now))
    notes: dict[str, ExitNote] = {}
    for exit_seen in exits:
        key = str(exit_seen.index)
        was = seen.exits.get(key)
        if exit_seen.state not in KNOWN_STATES:              # not known yet (just booted, a half-written file): as it was
            if was is not None:
                notes[key] = was
            continue
        if was is not None and was.state != exit_seen.state.value and seen.clients is not None:
            events.append(turned_event(exit_seen, was, now))
        still = was is not None and was.state == exit_seen.state.value and len(was.stored) > 2
        notes[key] = ExitNote.new(exit_seen.state, exit_seen.name, was.since_or(now) if still and was is not None else now)
    if not exits:
        notes = dict(seen.exits)                             # the cascade did not answer: as it was
    return Notes(at=now, clients=clients, exits=notes, leaving=leaving), events
