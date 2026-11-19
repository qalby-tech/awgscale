"""The gate's answer to a DNS query."""
from __future__ import annotations

from .settings import DnsgateSettings
from .sites import SiteList
from .upstream import Upstream
from .wire import ANSWERED_TYPES, RecordType, answer, parse_question


class Gate:
    def __init__(self, settings: DnsgateSettings, sites: SiteList, upstream: Upstream) -> None:
        self.__settings = settings
        self.__sites = sites
        self.__upstream = upstream

    async def resolve(self, message: bytes) -> bytes:
        """A listed name gets the proxy's address (or, for what is not an A record, nothing);
        everything else is the resolver's own answer. Raises OSError or struct.error when that cannot be had."""
        question = parse_question(message)
        if question and question.record_type in ANSWERED_TYPES and self.__sites.lists(question.name):
            return answer(message, question, self.__settings.proxy_ip if question.record_type == RecordType.A else None)
        return await self.__upstream.forward(message)
