"""The health of the exits as the files in the run directory hold it: a state and two
counters per exit. They outlive a restart of the service, and cascade.sh reads the same
files. Standard library only."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from .places import Places, read_text, write_text

FAILS_TO_DOWN = 2            # consecutive failed probes before an exit is DOWN
OKS_TO_UP = 4                # consecutive good probes before it is UP again


class ExitState(StrEnum):
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"      # no verdict yet: just booted, the link is not there or has not answered twice


def _counter(path: Path) -> int:
    try:
        return int(read_text(path).strip() or 0)
    except ValueError:
        return 0


class Health:
    def __init__(self, places: Places) -> None:
        self.__places = places

    def has_verdict(self, index: int) -> bool:
        return self.__places.exit_state(index).exists()

    def state(self, index: int) -> ExitState:
        text = read_text(self.__places.exit_state(index)).strip()
        return ExitState(text) if text in (ExitState.UP, ExitState.DOWN) else ExitState.UNKNOWN

    def is_up(self, index: int) -> bool:
        return self.state(index) is ExitState.UP

    def set_state(self, index: int, state: ExitState) -> None:
        write_text(self.__places.exit_state(index), state.value + "\n")

    def answered(self, index: int) -> int:
        """One more good probe. → how many in a row"""
        write_text(self.__places.exit_fails(index), "0\n")
        oks = _counter(self.__places.exit_oks(index)) + 1
        write_text(self.__places.exit_oks(index), f"{oks}\n")
        return oks

    def silent(self, index: int) -> int:
        """One more failed probe. → how many in a row"""
        write_text(self.__places.exit_oks(index), "0\n")
        fails = _counter(self.__places.exit_fails(index)) + 1
        write_text(self.__places.exit_fails(index), f"{fails}\n")
        return fails
