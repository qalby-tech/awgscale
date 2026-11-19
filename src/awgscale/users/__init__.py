"""The users service: who may connect to the tunnel.

Users (name, address, keys, term) live in Postgres; this service is the only
one that changes them. It answers HTTP on loopback (bearer token) and talks to
the kernel's tunnel directly:

    GET    /v1/health
    GET    /v1/users                         the users, in the order they were made
    POST   /v1/users          {name, expires}            make one ("7d", "12h", "4w" or null)
    DELETE /v1/users          {names: [...]}             remove
    POST   /v1/users/expiry   {names: [...], expires}    a new term from now, or null for none
    GET    /v1/users/<name>/config           what the device needs: config, vpn:// string, both QR pictures

Rules it never breaks:
  * the tunnel is changed one peer at a time (`awg set <if> peer …`); the
    interface is never restarted and never synced from a file;
  * <if>.conf stays what the tunnel boots from (it comes up before Docker). It
    is compiled here after every committed change: the [Interface] block is
    carried over verbatim, the peers come from the database. A render that
    would lose more peers than the operation removed is refused;
  * "could not read the users" is never "there are no users": with the
    database away nothing is rendered and nothing is removed;
  * a user is removed at the moment the term is over: every term is a scheduled
    job (APScheduler), nothing is polled;
  * peers the kernel does not have are put back when the service starts and
    when the tunnel's interface comes up (`ip monitor link`); a peer the
    service does not know is never removed.

    python -m awgscale.users                      serve
    python -m awgscale.users import <dir>         the one-time move from the installer's files
    python -m awgscale.users import-check <dir>   the same checks, nothing stored
"""
