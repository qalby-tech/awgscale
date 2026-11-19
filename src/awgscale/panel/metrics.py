"""The counters Prometheus scrapes, on a port of its own: never published and never behind Traefik."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Callable

from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse, Response

from awgscale.core.clock import utc_now
from awgscale.watch.health import ExitState

from .factory import get_state
from .looks import PanelState, ServerFacts, TrafficLook, WatchLook
from .state import StateBoard

FRESH = timedelta(seconds=30)      # a state older than this is not exported: no sample beats a wrong one
EXPOSITION = "text/plain; version=0.0.4; charset=utf-8"
NUMBER = (int, float)

Reading = Callable[[ServerFacts], int | float | None]


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _memory_used(server: ServerFacts) -> float | None:
    if server.mem_total and server.mem_used is not None and type(server.mem_used) in NUMBER:
        return server.mem_used / server.mem_total
    return None


def _disk_used(server: ServerFacts) -> float | None:
    return server.disk_pct / 100 if server.disk_pct is not None and type(server.disk_pct) in NUMBER else None


def _load(server: ServerFacts) -> int | float | None:
    return server.load1


SERVER_GAUGES: tuple[tuple[str, Reading], ...] = (
    ("vpn_server_load1", _load),
    ("vpn_server_memory_used_ratio", _memory_used),
    ("vpn_server_disk_used_ratio", _disk_used),
)


def _users(state: PanelState, traffic: TrafficLook | None) -> list[str]:
    """Per user, only the byte counters (the traffic history and the last-seen moments are made of them); the rest as totals."""
    lines = ["# TYPE vpn_user_bytes_total counter"]
    for name, received, sent in traffic.users if traffic is not None else []:
        if not (name and type(received) is int and type(sent) is int):
            continue
        label = _label(name)
        lines.append(f'vpn_user_bytes_total{{user="{label}",dir="down"}} {sent}')      # sent to the user = their download
        lines.append(f'vpn_user_bytes_total{{user="{label}",dir="up"}} {received}')
    users = state.users
    lines.append("# TYPE vpn_users_online gauge")
    lines.append(f"vpn_users_online {users.online if users is not None else 0}")
    if users is not None:
        lines += ["# TYPE vpn_users gauge", f"vpn_users {users.users}"]
    return lines


def _exits(state: PanelState) -> list[str]:
    exits = state.exits or []
    lines = ["# TYPE vpn_exit_up gauge"]
    for exit_ in exits:
        if exit_.name and exit_.state in (ExitState.UP, ExitState.DOWN):
            lines.append(f'vpn_exit_up{{exit="{_label(exit_.name)}"}} {int(exit_.state == ExitState.UP)}')
    # the watch service: how long an exit takes to answer its probe
    lines.append("# TYPE vpn_exit_probe_seconds gauge")
    for exit_ in exits:
        if exit_.name and exit_.probe_ms is not None and type(exit_.probe_ms) in NUMBER:
            lines.append(f'vpn_exit_probe_seconds{{exit="{_label(exit_.name)}"}} {exit_.probe_ms / 1000:.4f}')
    return lines


def _servers(state: PanelState) -> list[str]:
    """How the servers themselves are doing: the entry and every exit that tells."""
    servers: list[tuple[str, ServerFacts]] = [("entry", state.entry or ServerFacts())]
    servers += [(exit_.name, exit_.sys) for exit_ in state.exits or [] if exit_.name and exit_.sys is not None]
    lines: list[str] = []
    for metric, reading in SERVER_GAUGES:
        lines.append(f"# TYPE {metric} gauge")
        for name, server in servers:
            value = reading(server)
            if type(value) in NUMBER:
                lines.append(f'{metric}{{server="{_label(name)}"}} {value:.4f}')
    return lines


def _exit_clients(state: PanelState) -> list[str]:
    lines = ["# TYPE vpn_exit_clients gauge"]
    for exit_ in state.exits or []:
        if exit_.name and exit_.users is not None:
            lines.append(f'vpn_exit_clients{{exit="{_label(exit_.name)}"}} {exit_.users}')
    return lines


def _watch(state: PanelState) -> list[str]:
    """Whether the watch service itself is making passes."""
    watch = state.watch or WatchLook()
    alive = bool(watch.ok)
    lines = ["# TYPE vpn_watch_up gauge", f"vpn_watch_up {int(alive)}"]
    if alive and type(watch.last_tick) is int and watch.last_tick > 0:
        lines += ["# TYPE vpn_watch_last_tick_timestamp_seconds gauge", f"vpn_watch_last_tick_timestamp_seconds {watch.last_tick}"]
    if alive and type(watch.tick_seconds) in NUMBER:
        lines += ["# TYPE vpn_watch_tick_seconds gauge", f"vpn_watch_tick_seconds {watch.tick_seconds}"]
    if alive and type(watch.tick_failed) is int:
        lines += ["# TYPE vpn_watch_ticks_failed_total counter", f"vpn_watch_ticks_failed_total {watch.tick_failed}"]
    return lines


def is_fresh(state: PanelState, now: datetime) -> bool:
    return bool(state.ok) and not state.stale and state.fetched_at is not None and now - state.fetched_at < FRESH


def metrics_text(state: PanelState, now: datetime, traffic: TrafficLook | None) -> str:
    """The tunnel's byte counters per user name, as they are (Prometheus works
    out the growth and survives their resets). Byte counts and nothing else.
    A user the tunnel did not report, or a state that is stale, gives no line
    at all: a 0 in between would make the next real value look like growth of
    the whole counter."""
    fresh = is_fresh(state, now)
    lines = ["# TYPE vpn_panel_state_fresh gauge", f"vpn_panel_state_fresh {int(fresh)}"]
    if fresh:
        lines += _users(state, traffic) + _exits(state) + _servers(state) + _exit_clients(state) + _watch(state)
    return "\n".join(lines) + "\n"


# Prometheus asks /metrics and nothing else
metrics_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)


@metrics_app.get("/metrics")
async def metrics(state: Annotated[StateBoard, Depends(get_state)], now: Annotated[datetime, Depends(utc_now)]) -> Response:
    latest = state.latest
    traffic = await state.traffic() if is_fresh(latest, now) else None
    return PlainTextResponse(metrics_text(latest, now, traffic), media_type=EXPOSITION)
