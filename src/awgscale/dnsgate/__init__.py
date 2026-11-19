"""dnsgate — smart DNS for devices WITHOUT the VPN: a DNS-over-HTTPS resolver
and the SNI proxy that goes with it, in one process so they share one notion of
"who is allowed".

Runs as a container on the entry server behind Traefik:
    https://<domain>/dns-query/<personal code>     DoH (Traefik terminates TLS)
    any other TLS server name on :443               raw connection → SNI proxy

Who may use it
  * a device presents its personal code in the DoH address. The code travels
    inside TLS; the ISP cannot see it. Codes are issued/revoked in the panel.
  * the connection to a site carries no code, so the SNI proxy admits the
    address a valid code was seen from during the last ALLOW_HOURS.
  * addresses in STATIC_ALLOW (the admin's own machines) need no code.
  Everyone else: the resolver answers 404, the proxy closes the connection.

  * a name matching the shared site list (the one edited in the admin panel)
        A     → the entry server's public address, where the SNI proxy takes
                the connection and sends it abroad through the cascade
        AAAA / HTTPS / SVCB → empty answer (no IPv6 path, no ECH/alt-svc hints
                that would let the client bypass the proxy)
  * everything else → forwarded untouched to AdGuard Home (ad blocking, and
    the same split upstreams the VPN clients get)

List entries (one per line; address/network lines are for the VPN and ignored):
    example.com        the domain and all its subdomains
    *.example.com      same thing
    rr*.example.com    glob: `*` matches any run of characters inside the name
    *-pa.example.com   glob

The proxy only connects to names on the site list, port 443 — it is not a
general-purpose proxy even for admitted clients.

The site list and the codes are files the agent replaces; they are read again
the moment they change (the kernel says so; nothing is polled).

    python -m awgscale.dnsgate
"""
