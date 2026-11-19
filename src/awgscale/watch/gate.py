"""The boot gate: the rules once, and out — before the tunnel and Docker come up.

    python3 -m awgscale.watch.gate apply     install the rules; exit code 1 when they are not in
    python3 -m awgscale.watch.gate rules     print what apply would load, without touching anything

Run by the host's own Python with nothing installed: this module and everything
it imports use the standard library only.
"""
from __future__ import annotations

import logging
import sys
from enum import StrEnum

from awgscale.service.logs import setup_logging

from .cascade import Cascade
from .rules import mangle_rules

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
        sys.stdout.write(mangle_rules(cascade.conf()))
        return 0
    try:
        cascade.apply()
    except Exception as e:
        log.error("ERROR: rules not applied: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
