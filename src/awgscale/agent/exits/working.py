"""Which exits work right now, as the watch service says in its state files."""
from __future__ import annotations

import re
from pathlib import Path

from awgscale.watch.health import ExitState

# An exit's line in the compiled cascade.conf:
#     "3:203.0.113.20:10.77.3.2:exit2-nl:1"
EXIT_LINE_RE = re.compile(
    r"""
    "(?P<index>\d+)       # 3
    :(?P<iface>[^:"]+)    # the link's interface (older files: the server's address)
    :(?P<tip>[^:"]+)      # 10.77.3.2
    :(?P<name>[^:"]*)     # exit2-nl
    :?(?P<weight>\d*)"    # 1 — may be left out
    """,
    re.VERBOSE,
)


DEFAULT_WEIGHT = 1


class CompiledExits:
    """The exits of the compiled cascade.conf: what the routing knows, whatever the stored settings say."""

    def __init__(self, conf_file: Path) -> None:
        self.__conf_file = conf_file

    def weights(self) -> dict[int, int]:
        """{number: weight}; nothing without the file."""
        try:
            text = self.__conf_file.read_text()
        except OSError:
            return {}
        return {int(line["index"]): int(line["weight"] or DEFAULT_WEIGHT) for line in EXIT_LINE_RE.finditer(text)}


class StateFiles:
    """`exit<N>.state` beside the watch service's other files: one word, written at every change."""

    def __init__(self, run_dir: Path, conf_file: Path) -> None:
        self.__run_dir = run_dir
        self.__conf_file = conf_file

    def __compiled_indexes(self) -> list[int]:
        """The exits the routing knows: those of the compiled file, not of the stored settings."""
        try:
            text = self.__conf_file.read_text()
        except OSError:
            return []
        return [int(line["index"]) for line in EXIT_LINE_RE.finditer(text)]

    def up(self) -> set[int]:
        """Numbers of the exits the watch service calls working."""
        working: set[int] = set()
        for index in self.__compiled_indexes():
            try:
                state = (self.__run_dir / f"exit{index}.state").read_text().strip()
            except OSError:
                continue
            if state == ExitState.UP.value:
                working.add(index)
        return working
