"""The admin panel's server.

It hands out the page (a build of web/), keeps the admin's session, and passes
what the page asks on to the agent over its unix socket — after checking the
shape of every request, so nothing unexpected ever reaches the agent. It also
polls the state of the cascade for the page, asks Prometheus for the traffic
history, exports the counters Prometheus scrapes (a port of its own), and
serves the one part strangers reach: the invitation pages under /i/<secret>.

    python -m awgscale.panel
"""
