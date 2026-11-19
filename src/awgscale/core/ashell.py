"""Running the system's tools from asynchronous code: the same shape of answer as core.shell."""
from __future__ import annotations

import asyncio
from typing import Protocol, Sequence

from .shell import Result

DEFAULT_TIMEOUT = 30.0
TIMED_OUT = 124


class AsyncRunner(Protocol):
    """Anything that can run a command. The services take one in, so the tests can hand them a stand-in."""

    async def __call__(self, cmd: Sequence[str], stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> Result: ...


async def run(cmd: Sequence[str], stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> Result:
    """Run `cmd`. Never raises: a tool that is missing or hangs is a failed Result
    (a hung one is killed and waited for). Without input the child reads
    /dev/null, never this process's own stdin."""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return Result(1, "", str(e))
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin.encode() if stdin is not None else None), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return Result(TIMED_OUT, "", "timed out")
    return Result(process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace").strip())
