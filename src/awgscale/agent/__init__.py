"""The agent: carries out what the panel and the bot ask.

It listens on a unix socket (one JSON line in, one JSON line out), checks every
request against the model of its command, and runs it. It is the only writer
of the admin's settings; users, exits, routing and the resolver belong to
services of their own, which it asks over HTTP on loopback through typed
clients (awgscale.agent.clients).

    python -m awgscale.agent                 serve the socket
    python -m awgscale.agent <command> …     one command from a shell (deploy: store-init; see shell.py)

Every command is a handler of this package (factory.get_dispatcher): it takes
the command's request model and returns a typed answer, or refuses. What a
command needs of the world — the store, the other services, the host's tools —
it is handed through the protocols of ports.py.
"""
