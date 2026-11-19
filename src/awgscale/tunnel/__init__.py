"""The tunnel service: keeps tunnel interfaces there — the users' tunnel (awg0)
or, in its other role, the links to the exit servers (awgk*).

It takes the place of `awg-quick up`: the same steps, from the same file, in a
container on the host's network. The rules it never breaks:

  * an interface that is already there is TAKEN OVER as it is: nothing is set
    on it. If it is not what the file says, that is said loudly and nothing is
    changed — the users on it are not dropped by a service that merely started;
  * stopping the service leaves the interface and the rules where they are;
  * no rules, no tunnel: the users' tunnel is not made while the cascade's
    marking rule is not in the kernel. It waits; it does not give up;
  * the kernel module stays the host's business: without it making the link
    fails, and that is waited out as well;
  * the PostUp line of the file is never executed. The firewall rules it stands
    for are put in by this code; a PostUp that is not the known line is refused;
  * a link to an exit MUST say `Table = off` (this service adds no routes) and
    has no PostUp: routing into it is the watch service's.

What sets a look at an interface off: the start of the service; the kernel's
own word that the interface has gone, appeared or was switched (`ip monitor
link` — acted on at once, nothing is polled); and a clock (TUNNEL_PASS) for
what the kernel does not announce — a firewall rule gone, the cascade's rules
arriving, a new file.

    python -m awgscale.tunnel              the service
    python -m awgscale.tunnel check        one look, as JSON; exit 0 when all is as it should be
    python -m awgscale.tunnel strip-sum [<if>]   a fingerprint of what `awg setconf` would get
"""
