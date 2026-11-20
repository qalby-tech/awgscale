# How awgscale works, and why

## The pieces

```
                    ┌──────────────────────────── entry server ────────────────────────────┐
 devices ── awg0 ──▶│ kernel: AmneziaWG awg0, ipsets, iptables marks, policy routing       │── direct
                    │ watch     probes the exits, pins users to them, keeps the rules      │
                    │ resolver  AdGuard Home: DNS for the tunnel, filtering, fills ipsets  │── awgk1 ──▶ exit 1
                    │ users     the peers of awg0 (one at a time, never a restart)         │── awgk2 ──▶ exit 2
                    │ agent     the only writer of the settings; compiles them to files    │
                    │ panel     the web page and its API (behind Traefik)                  │
                    │ db        Postgres: groups, lists, users, invitations                │
                    │ exits     makes a rented server into an exit over SSH  (optional)    │
                    │ bot, smartdns, prometheus/loki/alloy/grafana, db-backup (optional)   │
                    └──────────────────────────────────────────────────────────────────────┘
```

Every service is the Python package `awgscale` (`src/awgscale/<name>`), one image each, all in
`stack/compose.yml`. The panel page is a Next.js static export (`web/`).

* **The panel and the bot talk only to the agent**, over a unix socket. The agent is the one place
  that changes settings: it stores them in Postgres and compiles what the kernel side reads
  (`cascade.conf`, the lists, the pins) into files.
* **The kernel side never reads the database.** `watch` and the boot gate work from the compiled
  files, so routing keeps working with Postgres, the agent or the panel down.
* **`users` owns the tunnel's peers**: it adds and removes one peer at a time (`awg set`) and keeps
  `awg0.conf` in step, so adding a user never restarts the interface.
* **`exits` is the only holder of the SSH key to the exit servers.** Adding an exit is a job: install
  AmneziaWG there, generate the link's keys, firewall the exit so that it answers only the entry,
  bring the link up, and only then let the agent put it into the settings.

## A connection, step by step

1. The device's DNS is the entry (`172.16.17.1`). AdGuard filters the name (ads, threats, the user's
   group level) and answers.
2. If the name is in a list that goes abroad, AdGuard also adds the answered addresses to the
   `force_exit` ipset (its `ipset` setting, compiled by the agent from the panel's groups).
3. The first packet of the connection passes the `CASCADE` chain in `mangle PREROUTING`:
   the tunnel's own network and private networks are left alone; an address in `force_exit` or
   `force_exit_net` (listed networks), or, in "everything foreign" mode, anything not in the `ru`
   set (the home country's networks, `HOME_COUNTRY`; the set keeps its first name), is marked.
4. The mark is first a fallback mark, then the mark of the exit the user is pinned to (the user's
   address is in that exit's `cx_<N>` set). `ip rule` sends each mark to its exit's table, whose
   default route is the link to that exit, with a blackhole underneath.
5. The mark is saved with the connection (`CONNMARK`) and restored for every later packet, so a
   connection stays on the exit it started on even when the user is moved to another one.
6. Unmarked traffic leaves the entry through its own NAT.

## Decisions

**AmneziaWG, not plain WireGuard.** WireGuard's handshake is easy to fingerprint and is dropped by
DPI in some networks. AmneziaWG keeps WireGuard's cryptography and kernel speed and disguises the
handshake (junk packets, header obfuscation). The stock AmneziaVPN apps import its keys from a QR
code or a `vpn://` link, so nobody needs a custom client to start.

**Routing on the server, not on the device.** The rules live in one place, change for everyone at
once, and work with the stock clients, which only know "send everything into the tunnel". The price:
everything passes the entry, and the device cannot choose by itself (see Known limits in the README).

**Names are routed through DNS answers, not by inspecting traffic.** The resolver puts the addresses
it answered for a listed name into an ipset; nothing reads SNI or decrypts anything, and it works for
any protocol, not only HTTPS. The price: a device that uses its own encrypted DNS bypasses the
lists; listed networks and "everything foreign" mode cover that case.

**Fail-closed.** Traffic that should go abroad is never sent direct when the exits are down: their
tables end in a blackhole and the fallback mark has no other way out. A site that is blocked at home
stays unreachable for a minute rather than leaking the user's home address to it. A single server
(`SINGLE_SERVER=1`) is the explicit opposite: there is nothing to fail over to, so nothing is marked.

**Health with hysteresis.** An exit is declared down after two silent probes and up again only after
four answers in a row, so users are not moved back and forth by a flapping link; a newly made link
gets no verdict until it exists.

**One link per exit, in the kernel.** Each exit has its own AmneziaWG interface (`awgk<N>`) with
its own keys. The exit sees one peer, the entry, and its firewall answers nobody else.

**Rules before the tunnel at boot.** `cascade-apply.service` runs the routing engine once on the
host's bare Python before Docker and before `awg0` comes up (`awg-quick@awg0` requires it): there is
no moment after a reboot when users are connected and nothing is routed. The engine is the very code
of the `watch` container, bundled into one standard-library-only file by the deploy. A reboot is done
under a dead-man's switch (`stack/deadman.sh`): if the server does not confirm itself healthy after
the boot, the previous gate is put back.

**Containers, built locally, switched one by one.** The images are built on the operator's machine
on the server's own Ubuntu release (iptables in the image must match the host's) and shipped with
`docker save | docker load`. A deploy switches only the services it is named, in a fixed order,
checks each one by asking it a real question, compares the running image ID with the shipped one,
and says how to go back if a check fails.

**The app knows a device by its tunnel address.** The awgscale app asks `/app/api/` on a Traefik
entrypoint of its own (`:8088`, plain HTTP: the tunnel is the encryption), which UFW opens only on
`awg0` and Traefik allow-lists to the tunnel and to the host `172.16.17.1` (a page whose name is
made to point there, DNS rebinding, is not served). Traefik sets `X-Real-Ip` from the connection and
replaces whatever the client sent, so the panel turns that address into a user without a password
or a token; a change also needs the app's own header, which a page of another site cannot send. The
answers are about that user only: their exit, their group (the other members only in a group that is
a space), whether they are an owner. The owner's first device joins with a one-time code that the
deploy prints when the network has no owner (`stack/owner-code.sh`): an invitation for one device
and 15 minutes that makes the user it creates an owner. An owner is remembered with the moment the
user was made, so a later user with the same name is not one.

**Counts, not browsing history.** The journal records changes and outages; the statistics are bytes
per user and visits per site for everyone together. Which user opened which site is not stored.

## Settings

* `site.env` (not in git): the server's name, the panel's allow-list, `FEATURES`.
* `.env` / `.env.example`: every setting of every service (pydantic-settings), with its default.
* `cascade/cascade.conf`: the seed of a new server; afterwards the settings live in the database and
  are changed in the panel or with `python -m awgscale.agent cascade-get / cascade-set`.
