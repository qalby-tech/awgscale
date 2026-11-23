"""The boot gate: the rules once, and out — before the tunnel and Docker come up.

    python3 -m awgscale.watch.gate apply     install the rules; exit code 1 when they are not in
    python3 -m awgscale.watch.gate rules     print what apply would load, without touching anything (the rules for
                                             everybody, then the chain of the rules for some groups)

Run by the host's own Python with nothing installed: this module and everything
it imports use the standard library only.
"""
from __future__ import annotations

import logging
import sys
from enum import StrEnum

from awgscale.service.logs import setup_logging

from .cascade import Cascade
from .places import read_text
from .rules import mangle_rules
from .scoped import parse_rules, scoped_payload

log = logging.getLogger("awgscale.watch")


class Command(StrEnum):
    APPLY = "apply"
    RULES = "rules"


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in tuple(Command):
        print(__doc__, file=sys.stderr)
        return 2
    setup_logging()
    cascade = Cascade()
    if Command(argv[0]) is Command.RULES:
        conf = cascade.conf()
        sys.stdout.write(mangle_rules(conf))
        if not conf.single_server:
            sys.stdout.write(scoped_payload(parse_rules(read_text(cascade.places.scoped_rules))))
        return 0
    try:
        cascade.apply()
    except Exception as e:
        log.error("ERROR: rules not applied: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
