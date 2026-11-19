"""The lock files of the agent: one writer at a time of what is read, changed and written back whole.

They are files (flock), not locks of this process: a command run from a shell
beside the daemon (a deploy, a timer) takes the very same ones, so a change
made here and a change made there never overwrite each other.
"""
from __future__ import annotations

import asyncio
import fcntl
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from .errors import Refused

LOCK_WAIT_SECONDS = 110.0          # whoever asked gave up at 150
RETRY_SECONDS = 0.05


@asynccontextmanager
async def file_lock(path: Path, wait_seconds: float = LOCK_WAIT_SECONDS) -> AsyncIterator[None]:
    """The lock, or a refusal once nobody is waiting for the answer any more: a
    change must not be applied minutes after its page showed an error. The loop
    is never blocked while it waits: whoever holds the lock may need it to finish."""
    deadline = time.monotonic() + wait_seconds
    with open(path, "w") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Refused("сервер занят другой операцией — попробуйте ещё раз") from None
                await asyncio.sleep(RETRY_SECONDS)
        yield
