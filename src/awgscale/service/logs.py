"""Lines for the container's log (collected by Loki): a UTC stamp, then what happened."""
from __future__ import annotations

import logging
import time

LINE_FORMAT = "%(asctime)s %(message)s"
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def setup_logging(level: int = logging.INFO) -> None:
    formatter = logging.Formatter(LINE_FORMAT, STAMP_FORMAT)
    formatter.converter = time.gmtime
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[handler], force=True)


class SaidOnce:
    """Lines of one logger, each led by who says it. With a `key` the same text
    is not repeated while it stays the same: a state that lasts is said once,
    not every pass."""

    def __init__(self, logger: logging.Logger, who: str) -> None:
        self.__logger = logger
        self.__who = who
        self.__said: dict[object, str] = {}

    def say(self, text: str, key: object = None) -> None:
        if key is not None:
            if self.__said.get(key) == text:
                return
            self.__said[key] = text
        self.__logger.info("%s: %s", self.__who, text)

    def forget(self, key: object) -> None:
        self.__said.pop(key, None)
