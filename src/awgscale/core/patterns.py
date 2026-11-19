"""The shapes of names the services check in the same way. Standard library only.

NAME_RE is kept on one line: the suites compare it, as text, with the services
the package replaced."""
from __future__ import annotations

import re

# a user:  alice   bob_2   phone-7  — never starts with "-": it becomes a command argument
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,31}\Z")
# an exit server:  exit1-nl   nl.2
EXIT_NAME_RE = re.compile(
    r"""
    ^[A-Za-z0-9._-]{1,32}\Z
    """,
    re.VERBOSE,
)
# a WireGuard key, base64 of 32 bytes:  yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
KEY_RE = re.compile(
    r"""
    ^[A-Za-z0-9+/]{43}      # yAnz5TF+…fBmk
    =\Z                     # =
    """,
    re.VERBOSE,
)
# a host name as the panel asks about it:  example.com   cdn.example.co.uk
HOST_RE = re.compile(
    r"""
    ^[a-z0-9]                         # e
    (?:[a-z0-9.-]{0,251}[a-z0-9])?    # xample.com — never ends in a dot or a hyphen
    \Z
    """,
    re.VERBOSE,
)
# the path of a page on that host, with its query:  /watch?v=abc
URL_PATH_RE = re.compile(
    r"""
    ^/[A-Za-z0-9._~%!$&'()*+,;=:@/?-]{0,600}\Z
    """,
    re.VERBOSE,
)
