"""awgscale — a two-tier AmneziaWG cascade: an entry server, exits abroad, and
the services that keep them: tunnels, routing, users, the panel, the bot.

Layout:
    awgscale.core      what every service shares; standard library only, because
                       the rules at boot are applied by the host's own Python
    awgscale.<service> one package per container; its settings are a
                       pydantic-settings class, its entry point is `python -m`
"""
