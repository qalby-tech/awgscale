"""The resolver's keeper: runs AdGuard Home as its child and lets the agent
stop and start it.

Why it exists: AdGuard reads the file of domains whose addresses it puts into
the routing sets only when it starts, and it has no call to restart itself. The
agent used to do that with `systemctl` on the host; now it asks here.

    HTTP (loopback, bearer token)
      GET  /v1/health   is it running and listening, since when, how often it had to be restarted
      POST /v1/stop     stop it; answers once the process is gone (it writes its
                        config on the way out, and the agent edits it next)
      POST /v1/start    start it; answers once it listens for DNS

What it guarantees on its own:
  - a resolver that dies is started again (at boot the tunnel's address may not
    be there yet: it just keeps trying);
  - a resolver that was stopped and never asked to start again is started by
    itself after RESOLVER_HOLD seconds — nobody is left without DNS by a caller that died;
  - asked to stop (SIGTERM), it lets AdGuard exit cleanly first.

    python -m awgscale.resolver
"""
