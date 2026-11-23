"""The cascade in the kernel: its rules put in, its exits probed, its clients pinned.
Every operation reads the settings as they are on disk at that moment. Standard library
only and synchronous — the boot gate runs `apply` from here on the host's bare Python."""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from awgscale.core.clock import utc_now
from awgscale.core.net import HOST_SUFFIX
from awgscale.service.errors import Refused

from . import lists
from .conf import DEFAULT_HOME_COUNTRY, CascadeConf, RouteMode
from .health import FAILS_TO_DOWN, OKS_TO_UP, ExitState, Health
from .lists import NET_OR_IP_RE, NET_RE
from .places import Places, flock, read_text, write_text
from .rules import (
    BLACKHOLE_METRIC, CHAIN, EXIT_METRIC, FALLBACK_MARK, FALLBACK_TABLE, NAT_CHAIN, SERVICE_TABLE,
    WHOLE_SET, Priority, exit_set, ip_key, mangle_rules, mark_of, nat_rules, pins_wanted, table_of, whole_wanted,
)
from .scoped import Scoped
from .spaces import Spaces
from .tools import IP_RE, Tools

log = logging.getLogger("awgscale.watch")

APPLY_LOCK_WAIT = 300.0
COMMAND_LOCK_WAIT = 60.0
PASS_LOCK_WAIT = 30.0
PING_TIMEOUT = 10.0


def _moment(unix_seconds: str) -> datetime | None:
    """A moment the awg tool printed as Unix seconds; "0" (never) and anything else is None."""
    return datetime.fromtimestamp(int(unix_seconds), UTC) if unix_seconds.isdigit() and int(unix_seconds) > 0 else None


@dataclass(slots=True)
class Stats:
    """What the engine remembers about its own work since this process started."""

    started: datetime = field(default_factory=utc_now)
    applied: datetime | None = None
    probe_ms: dict[int, float | None] = field(default_factory=dict)
    changed: dict[int, datetime] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExitStatus:
    index: int
    name: str
    iface: str
    state: ExitState
    handshake: datetime | None
    rx: int
    tx: int
    clients: tuple[str, ...]
    probe_ms: float | None
    changed: datetime | None


@dataclass(frozen=True, slots=True)
class CascadeStatus:
    time: datetime
    exits: tuple[ExitStatus, ...]
    udp_via_exit: bool
    route_mode: RouteMode
    force_exit_nets: int
    ru_networks: int
    force_exit_ips: int
    force_direct_ips: int
    single_server: bool = False
    home_country: str = DEFAULT_HOME_COUNTRY
    scoped_rules: int = 0
    scoped_ips: int = 0


@dataclass(frozen=True, slots=True)
class RuRefresh:
    ru_networks: int
    refreshed: bool


@dataclass(frozen=True, slots=True)
class NetsRefresh:
    nets: int
    refreshed: bool


@dataclass(frozen=True, slots=True)
class DomainsDone:
    nets: int                   # how many networks are listed
    missing: list[str]          # the sets of the rules for groups that could not be made or emptied


class Cascade:
    def __init__(self, places: Places | None = None, tools: Tools | None = None) -> None:
        self.__places = places or Places.from_environment()
        self.__tools = tools or Tools()
        self.__health = Health(self.__places)
        self.__spaces = Spaces(self.__places, self.__tools)
        self.__scoped = Scoped(self.__places, self.__tools)
        self.__kernel = threading.RLock()        # one writer of the sets, tables and chains inside this process
        self.__stats = Stats()

    @property
    def places(self) -> Places:
        return self.__places

    @property
    def stats(self) -> Stats:
        return self.__stats

    def conf(self) -> CascadeConf:
        return CascadeConf.load(self.__places)

    def fetch_home_zone(self, conf: CascadeConf) -> bool:
        return lists.fetch_home_zone(self.__places, conf.home_country)

    @contextlib.contextmanager
    def __alone(self, wait: float):
        """The kernel is this operation's alone: no other one of this process, and not cascade.sh. Refused when it did not come."""
        with self.__kernel, flock(self.__places.lock, wait) as got:
            if not got:
                raise Refused("занято другой операцией")
            yield

    # ── the home country's address list (the set keeps its first name, "ru") ──

    def __load_ru_set(self, conf: CascadeConf) -> None:
        tools = self.__tools
        zone = read_text(self.__places.home_zone(conf.home_country))
        if not zone.strip():
            raise Refused(f"список сетей страны {conf.home_country} пуст — отказ (с пустым списком за границу ушло бы всё)")
        tools.must(["ipset", "create", "ru", "hash:net", "-exist"])
        tools.must(["ipset", "create", "ru_tmp", "hash:net", "-exist"])
        tools.must(["ipset", "flush", "ru_tmp"])
        script = "".join(f"add ru_tmp {line} -exist\n" for line in zone.splitlines() if NET_RE.match(line))
        script += "".join(f"add ru_tmp {net} -exist\n" for net in conf.extra_ru_nets)
        tools.must(["ipset", "restore"], script)
        tools.must(["ipset", "swap", "ru_tmp", "ru"])
        tools.must(["ipset", "destroy", "ru_tmp"])
        log.info("Home country set (%s) loaded: %s networks", conf.home_country, tools.set_count("ru"))

    def update_ru(self) -> RuRefresh:
        conf = self.conf()
        if conf.single_server:          # nothing is routed by country: there is no list to keep
            return RuRefresh(ru_networks=0, refreshed=False)
        fresh = self.fetch_home_zone(conf)        # the download first: nothing waits for the network under the lock
        if not fresh:
            log.info("WARN: could not download the %s list — keeping the local copy", conf.home_country)
        with self.__alone(APPLY_LOCK_WAIT):
            self.__load_ru_set(conf)
        return RuRefresh(ru_networks=self.__tools.set_count("ru"), refreshed=fresh)

    # ── the rules ────────────────────────────────────────────────────────────

    def __ensure_exit_route(self, index: int, iface: str) -> None:
        """Default via the exit if its interface exists; the blackhole always stays underneath."""
        table = table_of(index)
        self.__tools.must(["ip", "route", "replace", "blackhole", "default", "metric", BLACKHOLE_METRIC, "table", table])
        if self.__tools.link_exists(iface):
            self.__tools.sh(["ip", "route", "replace", "default", "dev", iface, "metric", EXIT_METRIC, "table", table])

    def __load_exit_nets(self) -> None:
        tools = self.__tools
        places = self.__places
        tools.must(["ipset", "create", "force_exit_net", "hash:net", "-exist"])
        tools.must(["ipset", "create", "force_exit_net_tmp", "hash:net", "-exist"])
        tools.must(["ipset", "flush", "force_exit_net_tmp"])
        # the admin's list + networks maintained automatically (update-nets); the
        # automatic ones belong to a group in the panel, nets-auto.off is there
        # while that group is switched off
        sources = [places.listed]
        if not places.nets_auto_off.exists():
            sources.append(places.nets_auto)
        for source in sources:
            if not source.is_file():
                continue
            tools.must(["ipset", "restore"], "".join(f"add force_exit_net_tmp {line} -exist\n" for line in read_text(source).splitlines() if NET_OR_IP_RE.match(line)))
        tools.must(["ipset", "swap", "force_exit_net_tmp", "force_exit_net"])
        tools.must(["ipset", "destroy", "force_exit_net_tmp"])

    def __live(self, conf: CascadeConf) -> list[str]:
        """The tunnel addresses of the users the client tunnel has now (none before it exists, at boot)."""
        peers, _ = self.__tools.tunnel_peers(conf.client_if)
        return [a[:-len(HOST_SUFFIX)] for _, allowed in peers for a in allowed if a.endswith(HOST_SUFFIX) and IP_RE.match(a[:-len(HOST_SUFFIX)])]

    def __replace_rule(self, selector: list[str], priority: int) -> None:
        self.__tools.sh(["ip", "rule", "del", *selector])
        self.__tools.must(["ip", "rule", "add", *selector, "priority", str(priority)])

    def apply(self) -> RouteMode:
        """Installs the rules (idempotent, atomic). → the mode they were installed for"""
        tools = self.__tools
        places = self.__places
        conf = self.conf()
        if conf.single_server:
            return self.__apply_single(conf)
        if not read_text(places.home_zone(conf.home_country)).strip():   # otherwise the cached copy: a fast, network-independent start
            self.fetch_home_zone(conf)
        with self.__alone(APPLY_LOCK_WAIT):
            places.run.mkdir(parents=True, exist_ok=True)
            self.__load_ru_set(conf)

            # fallback table: blackhole until a pass points it at a healthy exit
            tools.must(["ip", "route", "replace", "blackhole", "default", "metric", BLACKHOLE_METRIC, "table", FALLBACK_TABLE])
            self.__replace_rule(["fwmark", FALLBACK_MARK, "table", FALLBACK_TABLE], Priority.FALLBACK)
            for e in conf.exits:
                table = table_of(e.index)
                self.__ensure_exit_route(e.index, e.iface)
                self.__replace_rule(["fwmark", mark_of(e.index), "table", table], Priority.MARK + e.index)
                self.__replace_rule(["from", e.tip, "table", table], Priority.FROM_EXIT + e.index)
                tools.must(["ipset", "create", exit_set(e.index), "hash:ip", "-exist"])
            # Local service subnets leave via an exit. Their table has NO blackhole:
            # with every exit down the lookup falls through to the main table, so
            # the bot can still try to report it. Traffic inside that network stays.
            for subnet in conf.via_exit_subnets:
                self.__replace_rule(["from", subnet, "table", SERVICE_TABLE], Priority.SERVICE)
                self.__replace_rule(["from", subnet, "to", subnet, "table", "main"], Priority.SERVICE_INSIDE)
            for upstream in conf.dns_via_exit:      # this server's own DNS upstream lookups go out through an exit
                self.__replace_rule(["iif", "lo", "to", upstream, "table", FALLBACK_TABLE], Priority.DNS)

            # Per-domain overrides. AdGuard fills these sets; they are created here
            # (AdGuard needs them to exist) and deliberately NOT flushed on apply.
            tools.must(["ipset", "create", "force_exit", "hash:ip", "-exist"])
            tools.must(["ipset", "create", "force_direct", "hash:ip", "-exist"])
            tools.must(["ipset", "create", "force_exit_net", "hash:net", "-exist"])
            tools.must(["ipset", "create", WHOLE_SET, "hash:ip", "-exist"])    # the rules name it; a pass fills it
            self.__scoped.ensure()          # the sets of the rules for groups (AdGuard's file names them) and their chain
            self.__load_exit_nets()
            saved = read_text(places.learned)     # learned addresses survive a reboot
            if saved:
                tools.sh(["ipset", "restore", "-exist"], saved)
            self.__scoped.restore_learned()

            tools.must(["iptables-restore", "--noflush"], mangle_rules(conf))
            if not tools.ok(["iptables", "-t", "mangle", "-C", "FORWARD", "-j", f"{CHAIN}-MSS"]):
                tools.must(["iptables", "-t", "mangle", "-A", "FORWARD", "-j", f"{CHAIN}-MSS"])
            from_clients = ["-i", conf.client_if, "-s", conf.client_subnet, "-j", CHAIN]
            if not tools.ok(["iptables", "-t", "mangle", "-C", "PREROUTING", *from_clients]):
                tools.must(["iptables", "-t", "mangle", "-I", "PREROUTING", "1", *from_clients])

            tools.must(["iptables-restore", "--noflush"], nat_rules(conf))
            if not tools.ok(["iptables", "-t", "nat", "-C", "POSTROUTING", "-j", NAT_CHAIN]):
                tools.must(["iptables", "-t", "nat", "-I", "POSTROUTING", "1", "-j", NAT_CHAIN])
            log.info("rules applied (%s exit(s), client subnet %s, mode %s)", len(conf.exits), conf.client_subnet, conf.mode.value)

            # Device spaces: deliberately last and never fatal — an extra, not a safety rule.
            if not self.__spaces.load(conf):
                log.info("WARN: device spaces were not loaded")
            # The rules for some groups: never fatal either — without them their people go by the rules for everybody.
            if not self.__scoped.load(conf, self.__live(conf)):
                log.info("WARN: rules for groups were not loaded")
        self.__stats.applied = utc_now()
        return conf.mode

    def __apply_single(self, conf: CascadeConf) -> RouteMode:
        """A single server: no country list, no exits, no marks. The chains are still there (the
        deploy and the tunnel look for them), the users' internet leaves from this server
        (the installer's NAT); the device spaces still apply."""
        tools = self.__tools
        with self.__alone(APPLY_LOCK_WAIT):
            self.__places.run.mkdir(parents=True, exist_ok=True)
            # AdGuard names these sets in its settings: they must exist even when nothing reads them
            tools.must(["ipset", "create", "force_exit", "hash:ip", "-exist"])
            tools.must(["ipset", "create", "force_direct", "hash:ip", "-exist"])
            tools.must(["ipset", "create", "force_exit_net", "hash:net", "-exist"])
            self.__scoped.ensure(chain=False)
            tools.must(["iptables-restore", "--noflush"], mangle_rules(conf))
            if not tools.ok(["iptables", "-t", "mangle", "-C", "FORWARD", "-j", f"{CHAIN}-MSS"]):
                tools.must(["iptables", "-t", "mangle", "-A", "FORWARD", "-j", f"{CHAIN}-MSS"])
            from_clients = ["-i", conf.client_if, "-s", conf.client_subnet, "-j", CHAIN]
            if not tools.ok(["iptables", "-t", "mangle", "-C", "PREROUTING", *from_clients]):
                tools.must(["iptables", "-t", "mangle", "-I", "PREROUTING", "1", *from_clients])
            tools.must(["iptables-restore", "--noflush"], nat_rules(conf))
            if not tools.ok(["iptables", "-t", "nat", "-C", "POSTROUTING", "-j", NAT_CHAIN]):
                tools.must(["iptables", "-t", "nat", "-I", "POSTROUTING", "1", "-j", NAT_CHAIN])
            log.info("rules applied (single server, client subnet %s)", conf.client_subnet)
            if not self.__spaces.load(conf):
                log.info("WARN: device spaces were not loaded")
        self.__stats.applied = utc_now()
        return conf.mode

    # ── health + pinning ─────────────────────────────────────────────────────

    def __probe(self, conf: CascadeConf, tip: str) -> float | None:
        """→ milliseconds of the first probe target that answered through that exit, or None."""
        for target in conf.probe_targets:
            started = time.monotonic()
            if self.__tools.ok(["ping", "-c1", "-W2", "-I", tip, target], timeout=PING_TIMEOUT):
                return round((time.monotonic() - started) * 1000, 1)
        return None

    def __probe_exits(self, conf: CascadeConf) -> None:
        tools = self.__tools
        health = self.__health
        healthy: list[str] = []
        for e in conf.exits:
            self.__ensure_exit_route(e.index, e.iface)
            first = not health.has_verdict(e.index)
            if first and not tools.link_exists(e.iface):
                # after a boot the link may be made a moment later than this service starts (both are
                # containers): a link that is not there yet is no verdict — the first probe once it
                # exists decides. Calling it DOWN here would keep a working exit out for a minute.
                continue
            was_up = health.is_up(e.index)
            now_up = was_up
            milliseconds = self.__probe(conf, e.tip)
            self.__stats.probe_ms[e.index] = milliseconds
            if milliseconds is not None:
                oks = health.answered(e.index)
                if (not was_up and oks >= OKS_TO_UP) or first:
                    self.__stats.changed[e.index] = utc_now()
                    now_up = True
                    log.info("exit %s (%s) is UP", e.index, e.iface)
            else:
                fails = health.silent(e.index)
                if first and fails < FAILS_TO_DOWN:
                    continue        # a link made a second ago may not have shaken hands yet: no first verdict from one probe
                if (was_up and fails >= FAILS_TO_DOWN) or first:
                    self.__stats.changed[e.index] = utc_now()
                    now_up = False
                    log.info("exit %s (%s) is DOWN", e.index, e.iface)
                    # its connections are dead; forget them so the apps reconnect at
                    # once through another exit instead of waiting for timeouts
                    tools.sh(["conntrack", "-D", "--mark", mark_of(e.index)])
            health.set_state(e.index, ExitState.UP if now_up else ExitState.DOWN)
            if now_up:
                healthy.append(e.iface)
        # the fallback table (unpinned clients + local DNS) follows the first healthy exit
        if healthy:
            tools.must(["ip", "route", "replace", "default", "dev", healthy[0], "metric", EXIT_METRIC, "table", FALLBACK_TABLE])
            tools.must(["ip", "route", "replace", "default", "dev", healthy[0], "table", SERVICE_TABLE])
        else:
            tools.sh(["ip", "route", "del", "default", "metric", EXIT_METRIC, "table", FALLBACK_TABLE])
            tools.sh(["ip", "route", "flush", "table", SERVICE_TABLE])

    def __pin_clients(self, conf: CascadeConf, ips: list[str]) -> None:
        tools = self.__tools
        up = {e.index for e in conf.exits if self.__health.is_up(e.index)}
        pins = read_text(self.__places.pins)
        wanted = pins_wanted(conf, ips, up, pins)
        present: dict[int, set[str]] = {}
        for e in conf.exits:
            tools.must(["ipset", "create", exit_set(e.index), "hash:ip", "-exist"])
            present[e.index] = set(tools.set_members(exit_set(e.index)))
            for ip in sorted(present[e.index], key=ip_key):
                if wanted.get(ip) != e.index:
                    # open connections are NOT touched: they stay on the exit they started on (connmark)
                    tools.sh(["ipset", "del", exit_set(e.index), ip])
                    log.info("client %s left exit %s → %s (new connections only)", ip, e.index, wanted.get(ip, "removed"))
        for ip in sorted(wanted, key=ip_key):
            if ip not in present.get(wanted[ip], ()):
                tools.must(["ipset", "add", exit_set(wanted[ip]), ip, "-exist"])
                log.info("client %s pinned to exit %s", ip, wanted[ip])
        # everything through the exit: an address leaves the set when its user changes their mind or is gone
        whole = whole_wanted(ips, pins)
        tools.must(["ipset", "create", WHOLE_SET, "hash:ip", "-exist"])
        whole_now = set(tools.set_members(WHOLE_SET))
        for ip in sorted(whole_now - whole, key=ip_key):
            tools.sh(["ipset", "del", WHOLE_SET, ip])
            log.info("client %s: as the network's mode (new connections only)", ip)
        for ip in sorted(whole - whole_now, key=ip_key):
            tools.must(["ipset", "add", WHOLE_SET, ip, "-exist"])
            log.info("client %s: everything through its exit (new connections only)", ip)
        self.__spaces.follow_peers(conf)

    def tick(self, probe: bool = True) -> bool:
        """One pass: the exits probed (unless `probe` is off), the clients pinned. One at a
        time — the scheduled pass, the agent and (while it exists) cascade.sh all come here.
        → False when another pass was running and this one was skipped"""
        conf = self.conf()
        self.__places.run.mkdir(parents=True, exist_ok=True)
        with self.__kernel, flock(self.__places.tick_lock, PASS_LOCK_WAIT) as got:
            if not got:
                return False
            if conf.single_server:      # no exits to probe or pin to: only the spaces follow the peers
                self.__spaces.follow_peers(conf)
                return True
            live: list[str] | None = None
            try:
                if probe:
                    self.__probe_exits(conf)
                live = self.__live(conf)
                self.__pin_clients(conf, live)
            finally:
                # who is in the groups of the rules for groups follows even when a step above failed
                self.__scoped.follow_members(conf, self.__live(conf) if live is None else live)
        return True

    def save_learned(self) -> None:
        """Remember the addresses learned for listed domains (they are restored by apply after a reboot)."""
        code, out = self.__tools.sh(["ipset", "save", "force_exit"])
        lines = [line for line in out.splitlines() if line.startswith("add ")] if code == 0 else []
        if lines:
            write_text(self.__places.learned, "\n".join(lines) + "\n")
        self.__scoped.save_learned()

    # ── device spaces, the listed networks ───────────────────────────────────

    def spaces_apply(self) -> int:
        """Loads the device spaces as compiled. → how many are active"""
        conf = self.conf()
        with self.__alone(COMMAND_LOCK_WAIT):
            if not self.__spaces.load(conf):
                raise Refused("пространства устройств не загрузились")
        active = self.__spaces.count()
        log.info("device spaces applied: %s active", active)
        return active

    def scoped_apply(self) -> int:
        """Loads the rules for some groups as compiled. → how many there are"""
        conf = self.conf()
        with self.__alone(COMMAND_LOCK_WAIT):
            rules, _ = self.__scoped.counts()
            if conf.single_server and not rules:
                return 0                    # nothing goes abroad here: nor does anything for some groups
            self.__scoped.ensure()
            if conf.single_server or not self.__scoped.load(conf, self.__live(conf)):
                raise Refused("правила для групп не загрузились")
        log.info("rules for groups applied: %s", rules)
        return rules

    def domains_reset(self) -> DomainsDone:
        """The agent calls this while the resolver is stopped: the listed networks
        are loaded again and the addresses learned for the old list are forgotten
        (the resolver learns the new list's as it starts). → how many networks are
        listed, and the sets of the rules for groups that could not be made"""
        tools = self.__tools
        with self.__alone(COMMAND_LOCK_WAIT):
            missing = self.__scoped.reset_names()       # first: the resolver is started after this step even when it fails, and refuses a set that is not there
            tools.must(["ipset", "create", "force_exit", "hash:ip", "-exist"])
            tools.must(["ipset", "create", "force_direct", "hash:ip", "-exist"])
            self.__load_exit_nets()
            tools.must(["ipset", "flush", "force_exit"])
            tools.must(["ipset", "flush", "force_direct"])
            self.__places.learned.unlink(missing_ok=True)
        return DomainsDone(nets=tools.set_count("force_exit_net"), missing=missing)

    def update_nets(self) -> NetsRefresh:
        """Refreshes the automatic networks (fetched through an exit) and loads them."""
        conf = self.conf()
        if conf.single_server:          # the automatic networks are fetched through an exit and routed to one
            return NetsRefresh(nets=0, refreshed=False)
        nets = lists.fetch_service_nets(self.__places, [e.tip for e in conf.exits])
        if nets:
            log.info("automatic networks updated: %s Telegram networks", len(nets))
        else:
            log.info("WARN: could not refresh the Telegram network list — keeping the previous one")
        with self.__kernel, flock(self.__places.lock, COMMAND_LOCK_WAIT) as got:
            if got:
                self.__load_exit_nets()
        return NetsRefresh(nets=len(nets), refreshed=bool(nets))

    # ── status ───────────────────────────────────────────────────────────────

    def __exit_status(self, index: int, name: str, iface: str) -> ExitStatus:
        tools = self.__tools
        code, out = tools.sh(["awg", "show", iface, "latest-handshakes"])
        handshake = out.split()[1] if code == 0 and len(out.split()) > 1 else "0"
        code, out = tools.sh(["awg", "show", iface, "transfer"])
        transfer = out.split() if code == 0 else []
        counted = len(transfer) > 2
        return ExitStatus(
            index=index,
            name=name,
            iface=iface,
            state=self.__health.state(index),
            handshake=_moment(handshake),
            rx=int(transfer[1]) if counted and transfer[1].isdigit() else 0,
            tx=int(transfer[2]) if counted and transfer[2].isdigit() else 0,
            clients=tuple(tools.set_members(exit_set(index))),
            probe_ms=self.__stats.probe_ms.get(index),
            changed=self.__stats.changed.get(index),
        )

    def status(self) -> CascadeStatus:
        tools = self.__tools
        conf = self.conf()
        scoped_rules, scoped_ips = self.__scoped.counts()
        return CascadeStatus(
            time=utc_now(),
            exits=tuple(self.__exit_status(e.index, e.name, e.iface) for e in conf.exits),
            udp_via_exit=conf.udp_via_exit,
            route_mode=conf.mode,
            force_exit_nets=tools.set_count("force_exit_net"),
            ru_networks=tools.set_count("ru"),
            force_exit_ips=tools.set_count("force_exit"),
            force_direct_ips=tools.set_count("force_direct"),
            single_server=conf.single_server,
            home_country=conf.home_country,
            scoped_rules=scoped_rules,
            scoped_ips=scoped_ips,
        )
