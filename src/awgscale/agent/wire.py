"""The answers of the other services in the shapes the socket has always sent.

The services answer with moments as ISO texts and flags as booleans; the panel
and the bot have always read Unix seconds and 1/0 from the agent. Inside the
package everything is typed; these functions are the one place where a typed
answer becomes the numbers of the wire.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from awgscale.users.models import Created, Removal
from awgscale.watch.models import ExitView, Work

NO_MOMENT = 0                   # how the wire says "never"


def unix(moment: datetime | None) -> int | None:
    """A moment as the wire holds it: whole Unix seconds."""
    return None if moment is None else int(moment.timestamp())


def unix_or_zero(moment: datetime | None) -> int:
    return NO_MOMENT if moment is None else int(moment.timestamp())


def unix_exact(moment: datetime | None) -> float | None:
    """The same, with the fraction: for the moments of the watch service, which the wire has always carried as time.time()."""
    return None if moment is None else moment.timestamp()


# ── answers of the services, in the shapes the panel and the bot read ────────

def created_shape(created: Created) -> dict[str, Any]:
    shape: dict[str, Any] = {"name": created.name, "status": created.status.value, "ip": str(created.ip), "expires_at": unix(created.expires_at)}
    if created.warning:
        shape["warning"] = created.warning
    return shape


def removal_shape(removal: Removal) -> dict[str, Any]:
    return removal.wire()


def exit_shape(exit_view: ExitView) -> dict[str, Any]:
    return {
        "index": exit_view.index,
        "name": exit_view.name,
        "iface": exit_view.iface,
        "state": exit_view.state.value,
        "handshake": unix_or_zero(exit_view.handshake),
        "rx": exit_view.rx,
        "tx": exit_view.tx,
        "clients": [str(address) for address in exit_view.clients],
        "probe_ms": exit_view.probe_ms,
        "changed": unix_exact(exit_view.changed),
    }


def work_shape(work: Work) -> dict[str, Any]:
    return {
        "started": unix_exact(work.started),
        "applied": unix_exact(work.applied) or NO_MOMENT,
        "last_tick": unix_exact(work.last_tick) or NO_MOMENT,
        "tick_seconds": work.tick_seconds,
        "ticks": work.ticks,
        "tick_failed": work.tick_failed,
    }
