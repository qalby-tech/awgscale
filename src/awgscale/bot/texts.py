"""The longer messages of the bot: the status, the users, the daily summary."""
from __future__ import annotations

from datetime import timedelta, tzinfo

from awgscale.core.clock import DAY, HOUR

from pydantic import BaseModel, Field

from .format import ago, duration_text, escaped, size_text
from .models import Bucket, ClientsPage, DayTotals, DayTraffic, HistoryDay, State

STALE_AFTER = timedelta(days=7)            # a user who has not connected for this long is named in the summary
EXPIRING_WITHIN = timedelta(days=3)        # a key that ends within this is named in the summary
TOP_USERS = 5
TOP_SITES = 5
TOP_BLOCKED = 3
STALE_SHOWN = 15
USERS_SHOWN = 40                           # /users: a page, never everybody


class Attention(BaseModel):
    """Who the summary names: asked of the agent, a page each."""

    stale: ClientsPage = Field(description="Those not heard from for a week, or never")
    soon: ClientsPage = Field(description="Those whose key ends within three days")


def routing_line(state: State) -> str:
    routing = state.routing
    if routing.by_list:
        return f"Через заграницу по списку: доменов {routing.exit_domains}, сетей {routing.exit_nets}; остальное напрямую"
    return "Через заграницу идёт всё зарубежное"


def status_text(state: State) -> str:
    now = state.time
    lines = ["<b>VPN-каскад</b>", f"Пользователи онлайн: <b>{state.users.online}</b> из {state.users.users}", ""]
    entry = state.entry
    if not entry.client_if_up:
        lines.append("🔴 Туннель для пользователей на входном сервере не поднят")
    for exit_ in state.exits:
        lines.append(
            f"{'🟢' if exit_.up else '🔴'} {escaped(exit_.name)}: {'работает' if exit_.up else 'недоступен'}, "
            f"пользователей {exit_.users}, связь {ago(exit_.handshake, now)}"
        )
    disk = f", диск {entry.disk_pct}%" if entry.disk_pct is not None else ""
    lines += ["", f"Вход: нагрузка {entry.load1} на {entry.cpus} ядра, память {entry.memory_percent}%{disk}"]
    dns = state.dns
    if dns.ok and dns.queries:
        lines.append(f"Реклама за 24 ч: заблокировано {100 * dns.blocked / dns.queries:.1f}% ({dns.blocked} из {dns.queries})")
    elif not dns.ok:
        lines.append("🔴 Блокировщик рекламы не отвечает")
    lines.append(routing_line(state))
    return "\n".join(lines)


def users_text(state: State, page: ClientsPage, zone: tzinfo, search: str = "") -> str:
    """A page of users (by address); the rest are counted, and found by a part of the name."""
    now = state.time
    exit_names = {exit_.index: exit_.name for exit_ in state.exits}
    if not page.clients:
        return "Никого не нашлось." if search else "Пользователей пока нет. Создать: /add имя"
    lines = ["<b>Пользователи</b>"]
    for client in page.clients:
        term = f", до {client.expires_at.astimezone(zone):%d.%m %H:%M}" if client.expires_at else ""
        lines.append(
            f"{'🟢' if client.online(now) else '⚪'} <code>{escaped(client.name)}</code> — {escaped(exit_names.get(client.exit, '—'))}, "
            f"{ago(client.handshake, now)}, ↓{size_text(client.tx)} ↑{size_text(client.rx)}{term}"
        )
    if page.total > len(page.clients):
        lines.append(f"… и ещё {page.total - len(page.clients)}. Поиск: /users часть имени")
    return "\n".join(lines)


def _users_part(state: State, bucket: Bucket, last_day: HistoryDay | None, totals: DayTotals, day: DayTraffic) -> list[str]:
    lines = [f"<b>Пользователи:</b> активных {totals.active} из {state.users.users}, пик онлайн {bucket.peak_online}"]
    traffic = f"<b>Трафик:</b> ↓ {size_text(totals.down)}  ↑ {size_text(totals.up)}"
    if last_day is not None:
        before = last_day.down + last_day.up
        if before > 0:
            traffic += f"  ({(totals.down + totals.up - before) / before * 100:+.0f}% к прошлым суткам)"
    lines.append(traffic)
    top = [usage for usage in day.top[:TOP_USERS] if usage.rx + usage.tx > 0]
    if top:
        lines.append("Больше всех:")
        lines += [f"  {place}. <code>{escaped(usage.name)}</code> — ↓{size_text(usage.tx)} ↑{size_text(usage.rx)}" for place, usage in enumerate(top, 1)]
    return lines


def _exits_part(state: State, bucket: Bucket) -> list[str]:
    span = max(1, (state.time - bucket.since).total_seconds())
    lines = [f"<b>Выходы:</b> сбоев за период — {bucket.incidents}"]
    for exit_ in state.exits:
        down = min(bucket.exit_down.get(exit_.name, 0), span)
        available = 100 * (1 - down / span)
        idle = f", простой {duration_text(down)}" if down else ""
        lines.append(f"  {'🟢' if exit_.up else '🔴'} {escaped(exit_.name)}: доступность {available:.1f}%{idle}, пользователей {exit_.users}")
    return lines


def _dns_part(state: State) -> list[str]:
    dns = state.dns
    if not dns.ok:
        return ["<b>Реклама:</b> 🔴 блокировщик не отвечает"]
    percent = 100 * dns.blocked / dns.queries if dns.queries else 0
    lines = [f"<b>Реклама (24 ч):</b> заблокировано {dns.blocked} из {dns.queries} запросов ({percent:.1f}%)"]
    if dns.top_blocked:
        lines.append("  чаще всего: " + ", ".join(escaped(blocked.domain) for blocked in dns.top_blocked[:TOP_BLOCKED]))
    return lines


def _sites_part(state: State) -> list[str]:
    lines: list[str] = []
    for title, sites in (("Через заграницу", state.dns.top_exit), ("Напрямую из России", state.dns.top_direct)):
        asked = [site for site in sites if site.count][:TOP_SITES]
        if asked:
            lines.append("")
            lines.append(f"<b>Популярные сайты · {title} (24 ч):</b>")
            lines += [f"  {place}. {escaped(site.name)} — {site.count}" for place, site in enumerate(asked, 1)]
    return lines


def _attention_part(attention: Attention, zone: tzinfo) -> list[str]:
    stale = [client.name for client in attention.stale.clients]
    soon = attention.soon.clients
    lines: list[str] = []
    if stale or soon:
        lines.append("")
    if soon:
        named = ", ".join(f"<code>{escaped(client.name)}</code> ({client.expires_at.astimezone(zone):%d.%m %H:%M})" for client in soon if client.expires_at)
        more = f" и ещё {attention.soon.total - len(soon)}" if attention.soon.total > len(soon) else ""
        lines.append("<b>Скоро истекают:</b> " + named + more)
    if stale:
        more = f" и ещё {attention.stale.total - STALE_SHOWN}" if attention.stale.total > STALE_SHOWN else ""
        named = ", ".join(f"<code>{escaped(name)}</code>" for name in stale[:STALE_SHOWN])
        lines.append(f"<b>Не подключались более {STALE_AFTER.days} дней:</b> {named}{more}")
    return lines


def report_text(state: State, bucket: Bucket, last_day: HistoryDay | None, zone: tzinfo, final: bool, day: DayTraffic, attention: Attention) -> tuple[str, DayTotals]:
    """The summary of what was counted in `bucket` (the users' traffic: by the agent, `day`); `final` is the daily one
    (it compares with the day before). → the text, and the day's totals for the history."""
    totals = DayTotals(down=day.down, up=day.up, active=day.active)
    title = "📊 <b>Сводка за сутки</b>" if final else f"📊 <b>Сводка с {bucket.since.astimezone(zone):%d.%m %H:%M}</b>"
    lines = [title, ""]
    lines += _users_part(state, bucket, last_day if final else None, totals, day)
    lines.append("")
    lines += _exits_part(state, bucket)
    lines.append("")
    lines += _dns_part(state)
    lines += _sites_part(state)
    lines += _attention_part(attention, zone)
    entry = state.entry
    disk = f", диск {entry.disk_pct}%" if entry.disk_pct is not None else ""
    lines += [
        "",
        f"<b>Входной сервер:</b> нагрузка {entry.load1} на {entry.cpus} ядра, память {entry.memory_percent}%{disk}"
        f", работает {entry.uptime // DAY} дн {entry.uptime % DAY // HOUR} ч",
    ]
    lines.append(routing_line(state))
    return "\n".join(lines), totals
