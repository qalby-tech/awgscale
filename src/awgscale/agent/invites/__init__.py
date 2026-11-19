"""Invitations: a link the admin hands out instead of a key.

Whoever opens it names a device and gets a user of their own, created at that
moment in the chosen group. The link works a set number of times until a set
day. Only a fingerprint of the link's secret is stored, so the store gives
nobody a working link. The page behind the link is public (it has to be), so
everything here assumes a stranger: the secret is 128 random bits, names are
checked as everywhere, a use is taken before the user is created and given
back if that fails, and there is a ceiling on how many users may exist at all.
"""
