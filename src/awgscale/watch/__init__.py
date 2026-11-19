"""The watch service: the kernel side of the routing on the entry server.

It owns the marking rules, the per-exit tables, the health of the exits, which
client leaves through which exit, the device spaces. It took the place of
`cascade.sh watch` and of the agent calling `cascade.sh <command>`.

    every 15 s      probe every exit, move clients off a dead one and back
    every 5 min     remember the addresses learned for the listed domains
    once a week     refresh the Russian address list and the automatic networks
    HTTP (loopback, bearer token)
        GET  /v1/health            is it running, were the rules applied
        GET  /v1/status            exits, their clients, counters of the sets
        POST /v1/apply             install the rules again (idempotent, atomic)
        POST /v1/pin               who goes where, from the health already known
        POST /v1/spaces            load the device spaces
        POST /v1/domains           the listed networks again; forget learned addresses
        POST /v1/update-ru         refresh the Russian address list now
        POST /v1/update-nets       refresh the automatic networks now

What it reads is compiled by the agent from the database (pins.map,
spaces.sets, domains-exit.txt, route.mode) or shipped by the deploy
(cascade.conf); it stores nothing of its own but the health counters in
/run/cascade, which outlive its restarts.

Two halves:
  * the engine — everything that touches the kernel (places, conf, tools,
    rules, lists, health, spaces, cascade). Standard library only and
    synchronous: the server's boot runs it before Docker exists, on the host's
    bare Python, as `python3 -m awgscale.watch.gate apply`;
  * the service around it (settings, models, service, api, app, __main__):
    FastAPI, with the engine's passes as scheduled jobs.

    python -m awgscale.watch            serve
    python -m awgscale.watch.gate apply the rules once, and out (the boot)
    python -m awgscale.watch.gate rules what apply would load, without touching anything
"""
