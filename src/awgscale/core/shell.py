"""Running the system's tools: one place, one shape of answer."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Result:
    """What a command gave back."""

    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class Runner(Protocol):
    """Anything that can run a command. The services take one in, so the tests can hand them a stand-in."""

    def __call__(self, cmd: Sequence[str], stdin: str | None = None, timeout: float = 30) -> Result: ...


def run(cmd: Sequence[str], stdin: str | None = None, timeout: float = 30) -> Result:
    """Run `cmd`. Never raises: a tool that is missing or hangs is a failed Result.
    Without input the child reads /dev/null, never this process's own stdin."""
    try:
        done = subprocess.run(
            list(cmd), capture_output=True, text=True, timeout=timeout,
            **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}),
        )
        return Result(done.returncode, done.stdout, (done.stderr or "").strip())
    except subprocess.TimeoutExpired:
        return Result(124, "", "timed out")
    except OSError as e:
        return Result(1, "", str(e))
