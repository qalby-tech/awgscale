"""The store: the admin's settings in Postgres.

What the admin edits — site groups, block groups, spaces, user groups, pauses,
invitations — is kept in tables, one set per kind. The agent works with each
kind as one ordered document: it is loaded whole and saved whole, in one
transaction. What the routing reads stays compiled into files next to the
rules: the tunnel must come up and keep routing with the database down.

A store that cannot be read is never "nothing stored": every reader raises
StoreDown, so nothing derived is recompiled from an empty answer. A database
without the "ready" mark (a fresh or lost volume) is refused the same way.

Two levels: documents as they are stored, record by record (documents.py) —
what is read back is what was written, whatever it held; and above it the
typed view of the kinds whose commands live in this package.
"""
