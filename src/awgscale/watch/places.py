"""Where the engine reads and writes: the cascade's directory and its run directory. Standard library only."""
from __future__ import annotations

import contextlib
import fcntl
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_DIR = "/root/awg/cascade"
DEFAULT_RUN = "/run/cascade"
LOCK_POLL_SECONDS = 0.2


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text()
    except OSError:
        return default


def write_text(path: Path, text: str) -> None:
    """Written beside and moved into place: nobody ever reads half a file."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


@contextlib.contextmanager
def flock(path: Path, wait: float) -> Iterator[bool]:
    """The same lock files cascade.sh takes: while both exist they must not
    work on the sets at the same time. → yields False when it did not come."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        deadline = time.monotonic() + wait
        got = False
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield got
        finally:
            if got:
                fcntl.flock(f, fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class Places:
    """`dir` holds what the agent compiles and the deploy ships; `run` holds the health counters, which outlive a restart."""

    dir: Path
    run: Path

    @classmethod
    def from_environment(cls) -> Places:
        return cls(Path(os.environ.get("CASCADE_DIR") or DEFAULT_DIR), Path(os.environ.get("CASCADE_RUN") or DEFAULT_RUN))

    @property
    def conf(self) -> Path:
        return self.dir / "cascade.conf"

    @property
    def route_mode(self) -> Path:
        return self.dir / "route.mode"

    def home_zone(self, country: str) -> Path:
        """The cached networks of the home country: ru.zone for Russia, as it always was."""
        return self.dir / f"{country}.zone"

    @property
    def pins(self) -> Path:
        return self.dir / "pins.map"

    @property
    def spaces(self) -> Path:
        return self.dir / "spaces.sets"

    @property
    def listed(self) -> Path:
        """The admin's list of domains and networks that go abroad."""
        return self.dir / "domains-exit.txt"

    @property
    def nets_auto(self) -> Path:
        return self.dir / "nets-auto.txt"

    @property
    def nets_auto_off(self) -> Path:
        """Present while the group the automatic networks belong to is switched off."""
        return self.dir / "nets-auto.off"

    @property
    def learned(self) -> Path:
        """The addresses learned for the listed domains, kept across a reboot."""
        return self.dir / "force_exit.save"

    @property
    def learned_scoped(self) -> Path:
        """The addresses learned for the names of the rules for some groups, kept across a reboot."""
        return self.dir / "scoped.save"

    @property
    def scoped_rules(self) -> Path:
        """The rules for some groups that go abroad or directly, in order (watch/scoped.py)."""
        return self.dir / "scoped.rules"

    @property
    def scoped_members(self) -> Path:
        """The tunnel addresses of each group's people."""
        return self.dir / "scoped.members"

    @property
    def domains_ipset(self) -> Path:
        """The resolver's file of sets, compiled by the agent: the sets it names must exist before it starts."""
        return self.dir / "domains.ipset"

    @property
    def lock(self) -> Path:
        return self.dir / ".lock"

    @property
    def tick_lock(self) -> Path:
        return self.run / "tick.lock"

    @property
    def peers_signature(self) -> Path:
        return self.run / "peers.sig"

    def exit_state(self, index: int) -> Path:
        return self.run / f"exit{index}.state"

    def exit_oks(self, index: int) -> Path:
        return self.run / f"exit{index}.ok"

    def exit_fails(self, index: int) -> Path:
        return self.run / f"exit{index}.fail"
