"""The boot gate as one file: what the entry server runs at boot, before Docker exists.

    python -m awgscale.watch.bundle <file>     write the bundle there
    python -m awgscale.watch.bundle -          write it to standard output

The file is a zip the host's own Python runs as it is:

    /usr/bin/python3 /opt/vpn-stack/boot/gate.pyz apply

Inside are the gate (awgscale.watch.gate) and the modules it imports — the
engine of the watch service and what it shares with the other services, the
standard library only. The same code gives the same bytes (the entries are
sorted and carry one fixed date), so two bundles can be compared as files:
the deploy installs a new one only when it differs, and the reboot safety net
keeps the copy a confirmed boot ran.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

PACKAGE_NAME = "awgscale"
PACKAGE_DIR = Path(__file__).resolve().parent.parent

# what the gate imports, as paths inside the package; nothing here may need more than the standard library
GATE_MODULES = (
    "__init__.py",
    "core/__init__.py",
    "core/clock.py",
    "core/net.py",
    "core/shell.py",
    "service/__init__.py",
    "service/errors.py",
    "service/logs.py",
    "watch/__init__.py",
    "watch/cascade.py",
    "watch/conf.py",
    "watch/gate.py",
    "watch/health.py",
    "watch/lists.py",
    "watch/places.py",
    "watch/rules.py",
    "watch/scoped.py",
    "watch/spaces.py",
    "watch/tools.py",
)

MAIN_NAME = "__main__.py"
MAIN_SOURCE = """import sys

from awgscale.watch.gate import main

sys.exit(main(sys.argv[1:]))
"""

ENTRY_DATE = (1980, 1, 1, 0, 0, 0)       # the earliest a zip can say: the bytes must not depend on when it was made
ENTRY_MODE = 0o644 << 16                 # a regular file, readable (the high half of a zip entry's attributes)
STANDARD_OUTPUT = "-"


def entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ENTRY_DATE)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = ENTRY_MODE
    return info


def bundle() -> bytes:
    """The gate and what it imports, as the bytes of one runnable zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(entry(MAIN_NAME), MAIN_SOURCE)
        for module in sorted(GATE_MODULES):
            archive.writestr(entry(f"{PACKAGE_NAME}/{module}"), (PACKAGE_DIR / module).read_bytes())
    return buffer.getvalue()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    made = bundle()
    if argv[0] == STANDARD_OUTPUT:
        sys.stdout.buffer.write(made)
    else:
        Path(argv[0]).write_bytes(made)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
