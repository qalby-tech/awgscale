"""A link to a block list, as the admin types it: taken only when it points at a file on an ordinary site."""
from __future__ import annotations

import asyncio

from ..sites.lists import ListLink, is_public, link_of


def clean_list_url(url: object) -> ListLink | None:
    """A link to a list file, or None: the same rules as a list of sites by link
    (https only, a real host name only, no address literal or other port)."""
    return link_of(url)


async def host_is_public(host: str) -> bool:
    """Every address of the name is an ordinary internet address."""
    return await asyncio.to_thread(is_public, host)
