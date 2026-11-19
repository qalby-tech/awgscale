"""The site list and the codes are files the agent replaces: they are read again the moment that happens."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, Mapping

from watchfiles import awatch

log = logging.getLogger("awgscale.dnsgate")

DEBOUNCE_MILLISECONDS = 100              # a file written beside and moved into place is one change, not several
RETRY_SECONDS = 3.0

Reload = Callable[[], None]


async def reload_on_change(files: Mapping[Path, Reload]) -> None:
    """Calls a file's reload every time the file changes (the kernel's own
    notifications for its directory; nothing is polled). It never dies: when
    the notifications cannot be had, everything is read again and it tries anew."""
    watched = {path.resolve(): reload for path, reload in files.items()}
    directories = {path.parent for path in watched}
    while True:
        try:
            async for changes in awatch(*directories, recursive=False, debounce=DEBOUNCE_MILLISECONDS):
                changed = {Path(path) for _, path in changes}
                for path, reload in watched.items():
                    if path in changed:
                        reload()
        except Exception as e:
            log.error("watching the files failed: %s", repr(e)[:200])
        await asyncio.sleep(RETRY_SECONDS)
        for reload in watched.values():     # a change may have been missed meanwhile
            reload()
