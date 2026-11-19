"""The exits service: makes a rented server into an exit of the cascade, and
looks after the exits it made — without anybody at a terminal.

It is the only place that holds the SSH key to the exit servers and the only
writer of the entry's side of the links (awgk<N>.conf and .key). The interface
itself is made by the links service the moment the file appears; routing into
it is the watch service's; the cascade's settings stay the agent's — this
service asks the agent to attach an exit once it is proven to work.

Rules it keeps:
  * one job at a time; every step can be run again;
  * an exit is attached to the cascade LAST, after the tunnel answers and the
    firewall let a new login in. A half-made exit never gets users;
  * the root password of a new server is used once, to put the key there. It is
    never written anywhere, never logged, and not kept after that step;
  * the key is accepted on an exit only from the entry's address, and the
    exit's host key is remembered at the first contact;
  * a server it did not make (managed = false) is never logged into.

    python -m awgscale.exits     the service (HTTP on loopback, a bearer token)
"""
