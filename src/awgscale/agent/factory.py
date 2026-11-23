"""The parts of this process, each made once."""
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from awgscale.core.ashell import run

from .adguard.api import AdGuard
from .app.owners import StoredOwners
from .app.service import AppService
from .adguard.client import AdGuardClient
from .adguard.profiles import AdGuardProfiles, ProfileMemory
from .adguard.group_lists import GroupBlockLists, GroupListsMemory, ListDownloader, ThreatsList
from .block.counters import AdGuardCounters
from .block.service import BlockService
from .block.store import StoredBlockGroups as OwnBlockGroups
from .cascade.service import CascadeService
from .cascade.store import StoredCascade
from .clients.exits import ExitsClient
from .clients.gates import WatchService
from .clients.http import ServiceClient
from .clients.resolver import ResolverClient
from .clients.users import UsersClient
from .clients.watch import WatchClient
from .commands import Command
from .daemon import Daemon
from .dispatcher import Dispatcher
from .exits.gate import ExitsService
from .exits.pins import KeptOnExit, StoredGroupExits
from .exits.service import ExitServers
from .exits.working import CompiledExits, StateFiles
from .dns.service import DnsDevicesService
from .dns.store import StoredDevices
from .handling import Handled
from .invites.handling import HandledFor
from .invites.newcomers import GuestUsers
from .invites.notes import JournalNotes
from .invites.service import InvitesService
from .invites.store import GroupMembersInStore, InviteRecordsInStore, InvitesInStore
from .journal.reader import JournalReader
from .journal.service import Journal
from .journal.writer import JournalWriter
from .seen.service import SeenNotes
from .seen.store import StoredNotes
from .settings import get_settings
from .shell import Shell, ShellCommand
from .spaces.gates import WatchSpaces
from .state.dns import DnsDay
from .state.ledger import Ledger
from .state.service import Sampling, StateService
from .state.system import Asked, Entry, ExitSystems
from .spaces.service import SpacesService
from .spaces.store import SpacesInStore
from .sites.gates import AdGuardBlocker, KeeperOfResolver, WatchForSites
from .sites.lists import ListReader
from .sites.probe import CurlProber
from .sites.routing import ResolverTuning, Routing, RoutingFiles
from .sites.service import SitesService
from .rules.service import RulesService
from .rules.members import StoredGroupMembers
from .rules.store import StoredRules
from .sites.store import StoredBlockGroups as BlockGroupsForSites, StoredMode, StoredSites
from .store.moving import StoreMoving
from .store.store import Store
from .tops.service import TopSites
from .tunnel import Tunnel
from .users.accounts import AccountsService
from .users.diagnosis import DiagnosisService
from .users.directory import Directory, UsersService as UsersGateway
from .users.forgetting import Forgetting
from .users.pauses import Pauses
from .users.pins import Pinning
from .users.regrouping import GroupsService
from .users.service import UsersService
from .users.store import StoredHandPins, StoredOwnFilters, StoredPauses, StoredUserGroups


@lru_cache
def get_users_client() -> UsersClient:
    settings = get_settings()
    return UsersClient(ServiceClient(str(settings.users_url), settings.users_token_file))


@lru_cache
def get_watch_client() -> WatchClient:
    settings = get_settings()
    return WatchClient(ServiceClient(str(settings.watch_url), settings.watch_token_file))


@lru_cache
def get_resolver_client() -> ResolverClient:
    settings = get_settings()
    return ResolverClient(ServiceClient(str(settings.resolver_url), settings.resolver_token_file))


@lru_cache
def get_exits_client() -> ExitsClient:
    settings = get_settings()
    return ExitsClient(ServiceClient(str(settings.exits_url), settings.exits_token_file))


@lru_cache
def get_journal_writer() -> JournalWriter:
    settings = get_settings()
    return JournalWriter(settings.journal_socket, settings.journal_id)


@lru_cache
def get_journal() -> Journal:
    settings = get_settings()
    return Journal(JournalReader(str(settings.loki_url), settings.journal_id, settings.journal_days), get_journal_writer())


@lru_cache
def get_store() -> Store:
    settings = get_settings()
    return Store(settings.store_dsn, settings.store_password_file)


@lru_cache
def get_cascade() -> CascadeService:
    settings = get_settings()
    return CascadeService(
        store=StoredCascade(get_store()),
        watch=WatchService(get_watch_client()),
        conf_file=settings.cascade_dir / "cascade.conf",
        lock_file=settings.cascade_dir / ".panel.lock",
    )


@lru_cache
def get_users() -> UsersService:
    settings = get_settings()
    store = get_store()
    return UsersService(
        directory=Directory(get_users_client(), settings.users_cache_file, settings.tunnel_file),
        users=UsersGateway(get_users_client()),
        groups=StoredUserGroups(store),
        hand_pins=StoredHandPins(store),
        cascade=StoredCascade(store),
        watch=WatchService(get_watch_client()),
        pause_store=StoredPauses(store),
        pauses=Pauses(StoredPauses(store), run, settings.client_iface, settings.cascade_dir / "suspended.ips"),
        pins_file=settings.cascade_dir / "pins.map",
        lock_file=settings.cascade_dir / ".users.lock",
    )


# ── the exits as servers ─────────────────────────────────────────────────────
@lru_cache
def get_exit_servers() -> ExitServers:
    settings = get_settings()
    store = get_store()
    watch = WatchService(get_watch_client())
    kept = KeptOnExit(
        groups=StoredGroupExits(store),
        hand_pins=StoredHandPins(store),
        directory=Directory(get_users_client(), settings.users_cache_file, settings.tunnel_file),
        watch=watch,
        pins_file=settings.cascade_dir / "pins.map",
        lock_file=settings.cascade_dir / ".users.lock",
    )
    return ExitServers(
        cascade=get_cascade(),
        settings=StoredCascade(store),
        exits=ExitsService(get_exits_client()),
        working=StateFiles(settings.run_dir, settings.cascade_dir / "cascade.conf"),
        kept=kept,
    )


# ── spaces and invitations ───────────────────────────────────────────────────

@lru_cache
def get_spaces() -> SpacesService:
    settings = get_settings()
    store = get_store()
    return SpacesService(
        directory=Directory(get_users_client(), settings.users_cache_file, settings.tunnel_file),
        spaces=SpacesInStore(store),
        groups=StoredUserGroups(store),
        rules=WatchSpaces(get_watch_client()),
        run=run,
        client_iface=settings.client_iface,
        sets_file=settings.cascade_dir / "spaces.sets",
        lock_file=settings.cascade_dir / ".users.lock",
    )


@lru_cache
def get_invites() -> InvitesService:
    settings = get_settings()
    store = get_store()
    return InvitesService(
        invites=InvitesInStore(store),
        groups=GroupMembersInStore(store),
        directory=Directory(get_users_client(), settings.users_cache_file, settings.tunnel_file),
        users=UsersGateway(get_users_client()),
        newcomers=GuestUsers(store, get_forgetting(), get_seen(), UsersGateway(get_users_client()), get_pinning(), get_filter_profiles()),
        watch=WatchService(get_watch_client()),
        spaces=SpacesInStore(store),
        space_rules=get_spaces(),
        notes=JournalNotes(get_journal()),
        users_lock=settings.cascade_dir / ".users.lock",
        invite_lock=settings.cascade_dir / ".invite.lock",
        owners=StoredOwners(store),
    )


@lru_cache
def get_app() -> AppService:
    settings = get_settings()
    store = get_store()
    return AppService(
        directory=get_directory(),
        groups=StoredUserGroups(store),
        hand_pins=StoredHandPins(store),
        owners=StoredOwners(store),
        cascade=StoredCascade(store),
        working=StateFiles(settings.run_dir, settings.cascade_dir / "cascade.conf"),
        users=get_users(),
        own_filters=StoredOwnFilters(store),
        profiles=get_filter_profiles(),
        users_lock=settings.cascade_dir / ".users.lock",
        pinning=get_pinning(),
    )


def register_spaces_and_invites(dispatcher: Dispatcher, journal: Journal) -> None:
    spaces = get_spaces()
    dispatcher.register(Command.SPACES_GET, Handled(spaces.show))
    dispatcher.register(Command.SPACES_SET, Handled(spaces.replace, journal, before=spaces.stored_before))
    invites = get_invites()
    dispatcher.register(Command.INVITE_LIST, Handled(invites.list))
    dispatcher.register(Command.INVITE_ADD, HandledFor(invites.add))
    dispatcher.register(Command.INVITE_REMOVE, HandledFor(invites.remove))
    dispatcher.register(Command.INVITE_INFO, Handled(invites.info))
    dispatcher.register(Command.INVITE_REDEEM, Handled(invites.redeem))


# ── sites and routing ────────────────────────────────────────────────────────

@lru_cache
def get_sites() -> SitesService:
    settings = get_settings()
    store = get_store()
    cascade = settings.cascade_dir
    files = RoutingFiles(
        domains_exit=cascade / "domains-exit.txt",
        domains_direct=cascade / "domains-direct.txt",
        domains_direct_rules=cascade / "domains-direct-rules.txt",
        nets_auto=cascade / "nets-auto.txt",
        nets_auto_off=cascade / "nets-auto.off",
        domains_ipset=cascade / "domains.ipset",
        domains_shared=settings.lists_dir / "domains-exit.txt",
        resolver_yaml=settings.resolver_yaml,
        route_mode=cascade / "route.mode",
    )
    blocker = AdGuardBlocker(get_adguard_client())
    tuning = ResolverTuning(blocker, run, settings.client_iface, files.route_mode)
    return SitesService(
        store=StoredSites(store),
        modes=StoredMode(store),
        routing=Routing(files, WatchForSites(get_watch_client()), KeeperOfResolver(get_resolver_client()), tuning),
        lists=ListReader(run, cascade / "cascade.conf", settings.run_dir),
        blocker=blocker,
        block_groups=BlockGroupsForSites(store),
        prober=CurlProber(run),
        cascade_conf=cascade / "cascade.conf",
        lock_file=cascade / ".panel.lock",
        rules=StoredRules(store),
        block_rules=get_block(),
    )


# ── blocking (AdGuard) and the smart-DNS devices ─────────────────────────────

@lru_cache
def get_adguard_client() -> AdGuardClient:
    settings = get_settings()
    return AdGuardClient(str(settings.adguard_url), settings.adguard_credentials_file)


@lru_cache
def get_adguard() -> AdGuard:
    return AdGuard(get_adguard_client())


@lru_cache
def get_group_lists() -> GroupBlockLists:
    """The groups' own lists (the levels, the children's add-on lists), made into one file for AdGuard."""
    settings = get_settings()
    return GroupBlockLists(
        adguard=get_adguard(),
        groups=StoredUserGroups(get_store()),
        journal=get_journal(),
        downloader=ListDownloader(str(settings.hagezi_url), settings.group_lists_max_bytes),
        file=settings.group_lists_file,
        min_rules=settings.group_lists_min_rules,
        memory=GroupListsMemory(),
        own=StoredOwnFilters(get_store()),
    )


@lru_cache
def get_threats_list() -> ThreatsList:
    """«Угрозы», the list for everybody."""
    return ThreatsList(get_adguard(), str(get_settings().threats_list_url))


@lru_cache
def get_filter_profiles() -> AdGuardProfiles:
    """How each group of users is filtered (ports.FilterProfiles)."""
    settings = get_settings()
    return AdGuardProfiles(
        clients=get_adguard(),
        groups=StoredUserGroups(get_store()),
        journal=get_journal(),
        lock_file=settings.cascade_dir / ".users.lock",
        memory=ProfileMemory(),
        group_lists=get_group_lists(),
        own=StoredOwnFilters(get_store()),
        routes=StoredHandPins(get_store()),
        blocking=get_block(),
    )


@lru_cache
def get_top_sites() -> TopSites:
    settings = get_settings()
    return TopSites(
        log_file=settings.adguard_querylog_file,
        kept_file=settings.run_dir / "top-sites.json",
        conf_file=settings.cascade_dir / "cascade.conf",
        mode_file=settings.cascade_dir / "route.mode",
        sites=StoredSites(get_store()),
    )


@lru_cache
def get_block() -> BlockService:
    return BlockService(
        adguard=get_adguard(),
        groups=OwnBlockGroups(get_store()),
        counters=AdGuardCounters(get_adguard()),
        tops=get_top_sites(),
        filters_dir=get_settings().adguard_filters_dir,
        rules=StoredRules(get_store()),
        members=StoredGroupMembers(StoredUserGroups(get_store()), get_directory()),
    )


@lru_cache
def get_dns_devices() -> DnsDevicesService:
    settings = get_settings()
    return DnsDevicesService(StoredDevices(get_store()), settings.dns_tokens_file, settings.dns_state_file)


# ── users made and removed, and what is kept in step with them ───────────────

@lru_cache
def get_directory() -> Directory:
    settings = get_settings()
    return Directory(get_users_client(), settings.users_cache_file, settings.tunnel_file)


@lru_cache
def get_seen() -> SeenNotes:
    store = get_store()
    return SeenNotes(StoredNotes(store), store, get_journal(), get_settings().cascade_dir / ".users.lock")


@lru_cache
def get_pinning() -> Pinning:
    return Pinning(StoredHandPins(get_store()), WatchService(get_watch_client()), get_settings().cascade_dir / "pins.map")


@lru_cache
def get_forgetting() -> Forgetting:
    settings = get_settings()
    store = get_store()
    return Forgetting(
        directory=get_directory(),
        hand_pins=StoredHandPins(store),
        pause_store=StoredPauses(store),
        pauses=Pauses(StoredPauses(store), run, settings.client_iface, settings.cascade_dir / "suspended.ips"),
        spaces=SpacesInStore(store),
        groups=GroupMembersInStore(store),
        pinning=get_pinning(),
        profiles=get_filter_profiles(),
        space_rules=get_spaces(),
        together=store,
        lock_file=settings.cascade_dir / ".users.lock",
        own_filters=StoredOwnFilters(store),
    )


@lru_cache
def get_accounts() -> AccountsService:
    return AccountsService(
        store=get_store(),
        directory=get_directory(),
        users=UsersGateway(get_users_client()),
        forgetting=get_forgetting(),
        seen=get_seen(),
        watch=WatchService(get_watch_client()),
    )


@lru_cache
def get_tunnel() -> Tunnel:
    return Tunnel(run, get_settings().client_iface)


@lru_cache
def get_diagnosis() -> DiagnosisService:
    return DiagnosisService(get_directory(), get_tunnel(), StoredPauses(get_store()), WatchService(get_watch_client()), get_adguard())


@lru_cache
def get_groups() -> GroupsService:
    settings = get_settings()
    store = get_store()
    return GroupsService(
        directory=get_directory(),
        groups=GroupMembersInStore(store),
        invitations=InviteRecordsInStore(store),
        exits=CompiledExits(settings.cascade_dir / "cascade.conf"),
        pinning=get_pinning(),
        spaces=SpacesInStore(store),
        space_rules=get_spaces(),
        profiles=get_filter_profiles(),
        together=store,
        lock_file=settings.cascade_dir / ".users.lock",
    )


# ── the state ────────────────────────────────────────────────────────────────

@lru_cache
def get_state() -> StateService:
    settings = get_settings()
    store = get_store()
    cascade = settings.cascade_dir
    asked: Asked = {}
    return StateService(
        watch=WatchService(get_watch_client()),
        exits=CompiledExits(cascade / "cascade.conf"),
        tunnel=get_tunnel(),
        entry=Entry(run),
        directory=get_directory(),
        pins=get_users(),
        seen=get_seen(),
        profiles=get_filter_profiles(),
        exit_servers=ExitSystems(run, settings.exit_stats_port, asked),
        pause_store=StoredPauses(store),
        pauses=Pauses(StoredPauses(store), run, settings.client_iface, cascade / "suspended.ips"),
        hand_pins=StoredHandPins(store),
        groups=StoredUserGroups(store),
        sites=StoredSites(store),
        spaces=SpacesInStore(store),
        dns=DnsDay(get_top_sites(), get_adguard()),
        client_iface=settings.client_iface,
        domains_exit=cascade / "domains-exit.txt",
        domains_direct=cascade / "domains-direct.txt",
        lock_file=cascade / ".users.lock",
        sampling=Sampling(
            every=timedelta(seconds=settings.sample_seconds),
            online_for=timedelta(seconds=settings.online_seconds),
            keep_day_every=timedelta(seconds=settings.day_keep_seconds),
        ),
        ledger=get_ledger(),
    )


@lru_cache
def get_ledger() -> Ledger:
    ledger = Ledger(get_settings().day_file)
    ledger.load()
    return ledger


# the commands that only read: after any other one the next question is answered from a new look
READING = frozenset({
    Command.STATE, Command.CLIENTS, Command.DAY, Command.TRAFFIC, Command.JOURNAL, Command.QR, Command.DIAG, Command.UGROUPS_GET,
    Command.SPACES_GET, Command.SITES_GET, Command.CHECK, Command.PROBE, Command.BLOCK_GET, Command.DNS_LIST, Command.INVITE_LIST,
    Command.INVITE_INFO, Command.EXITS_GET, Command.APP_ME, Command.APP_EXITS,
})


@lru_cache
def get_dispatcher() -> Dispatcher:
    """Every command of the socket with its handler."""
    dispatcher = Dispatcher()
    journal = get_journal()
    state = get_state()
    dispatcher.register(Command.STATE, Handled(state.show))
    dispatcher.register(Command.CLIENTS, Handled(state.clients))
    dispatcher.register(Command.DAY, Handled(state.day))
    dispatcher.register(Command.TRAFFIC, Handled(state.traffic))
    dispatcher.after_change(state.changed, READING)
    dispatcher.register(Command.JOURNAL, journal.show)
    dispatcher.register(Command.WEIGHTS_SET, Handled(get_cascade().set_weights, journal))
    dispatcher.register(Command.COUNTRY_SET, Handled(get_cascade().set_country, journal))
    users = get_users()
    dispatcher.register(Command.UGROUPS_GET, Handled(users.show_groups))
    dispatcher.register(Command.USER_EXIT, Handled(users.choose_exit, journal))
    dispatcher.register(Command.EXPIRE, Handled(users.set_term, journal))
    dispatcher.register(Command.QR, Handled(users.device_code))
    dispatcher.register(Command.SUSPEND, Handled(users.pause, journal))
    dispatcher.register(Command.DIAG, Handled(get_diagnosis().check))
    groups = get_groups()
    dispatcher.register(Command.UGROUPS_SET, Handled(groups.replace, journal, before=groups.stored_before))
    accounts = get_accounts()
    dispatcher.register(Command.ADD, Handled(accounts.add, journal))
    dispatcher.register(Command.REMOVE, Handled(accounts.remove, journal))
    # the exits as servers
    exit_servers = get_exit_servers()
    dispatcher.register(Command.EXITS_GET, Handled(exit_servers.show))
    dispatcher.register(Command.EXIT_ADD, Handled(exit_servers.add, journal, with_secret=True))
    dispatcher.register(Command.EXIT_ATTACH, Handled(exit_servers.attach, journal))
    dispatcher.register(Command.EXIT_RENAME, Handled(exit_servers.rename, journal))
    dispatcher.register(Command.EXIT_REMOVE, Handled(exit_servers.remove, journal))
    dispatcher.register(Command.EXIT_UPDATE, Handled(exit_servers.update, journal))
    # blocking (AdGuard) and the smart-DNS devices
    block = get_block()
    dispatcher.register(Command.BLOCK_GET, Handled(block.show))
    dispatcher.register(Command.BLOCK_SET, Handled(block.save, journal))
    dispatcher.register(Command.BLOCK_LISTS, Handled(block.change_lists, journal))
    devices = get_dns_devices()
    dispatcher.register(Command.DNS_LIST, Handled(devices.show))
    dispatcher.register(Command.DNS_ADD, Handled(devices.add, journal))
    dispatcher.register(Command.DNS_REMOVE, Handled(devices.remove, journal))
    register_spaces_and_invites(dispatcher, journal)
    # sites and routing
    sites = get_sites()
    dispatcher.register(Command.SITES_GET, Handled(sites.show))
    dispatcher.register(Command.SITES_SET, Handled(sites.save, journal, before=sites.stored_records))
    dispatcher.register(Command.SITES_REFRESH, Handled(sites.refresh, journal, before=sites.stored_records))
    dispatcher.register(Command.MODE_SET, Handled(sites.set_mode, journal))
    dispatcher.register(Command.CHECK, Handled(sites.check))
    dispatcher.register(Command.PROBE, Handled(sites.probe))
    # the rules as one list: where each leads, and in which order
    # the lock of the sites: a save of «Сайты» or the nightly refresh of the lists waits for a whole change of the rules
    rules = RulesService(
        store=StoredRules(get_store()),
        routing=sites,
        blocking=block,
        lock_file=get_settings().cascade_dir / ".panel.lock",
        groups=StoredUserGroups(get_store()),
    )
    dispatcher.register(Command.RULES_GET, Handled(rules.show))
    dispatcher.register(Command.RULE_ROUTE, Handled(rules.route, journal))
    # the app on a user's own device
    app = get_app()
    dispatcher.register(Command.APP_ME, Handled(app.me))
    dispatcher.register(Command.APP_EXITS, Handled(app.exits))
    dispatcher.register(Command.APP_EXIT, Handled(app.choose, journal))
    dispatcher.register(Command.APP_FILTER, Handled(app.choose_filter, journal))
    dispatcher.register(Command.APP_ROUTE, Handled(app.choose_route, journal))
    without_handler = frozenset(Command) - dispatcher.commands
    if without_handler:
        raise RuntimeError("commands without a handler: " + ", ".join(sorted(command.value for command in without_handler)))
    return dispatcher


@lru_cache
def get_shell() -> Shell:
    """The commands only a shell may run, beside those of the socket."""
    settings = get_settings()
    journal = get_journal()
    moving = StoreMoving(get_store(), settings.cascade_dir, settings.dns_tokens_file)
    cascade = get_cascade()
    shell_commands = Dispatcher()
    shell_commands.register(ShellCommand.STORE_INIT, Handled(moving.init))
    shell_commands.register(ShellCommand.STORE_EXPORT, Handled(moving.export))
    shell_commands.register(ShellCommand.CASCADE_GET, Handled(cascade.show))
    shell_commands.register(ShellCommand.CASCADE_SET, Handled(cascade.replace, journal))
    shell_commands.register(ShellCommand.SPACES_SYNC, Handled(get_spaces().resync))
    shell_commands.register(ShellCommand.SUSPEND_SYNC, Handled(get_users().resync_pauses))
    shell_commands.register(ShellCommand.OWNER_CODE, Handled(get_invites().owner_code))
    return Shell(get_dispatcher(), shell_commands)


@lru_cache
def get_daemon() -> Daemon:
    settings = get_settings()
    return Daemon(get_dispatcher(), settings.socket_path, settings.backlog)
