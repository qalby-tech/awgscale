"""The exits as servers: a rented server becomes an exit, an exit is renamed, updated, removed.

The work on a server itself (SSH, packages, the tunnel, the firewall) is the
exits service's: it holds the key, runs the long jobs and writes the entry's
side of a link. The agent stays the only writer of the cascade's settings: the
service asks it to attach an exit once that exit works.
"""
