"""python -m awgscale.tunnel [check | strip-sum [<if>]]"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from enum import StrEnum

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from awgscale.service.logs import setup_logging

from .keeper import Keeper
from .settings import Role, get_settings


class Command(StrEnum):
    CHECK = "check"
    STRIP_SUM = "strip-sum"


async def check(keeper: Keeper) -> int:
    """One look, printed as JSON. Exit 0 when everything is as it should be;
    with the exit links, nothing to keep is not "fine"."""
    statuses = [(await keeper.look(iface)).status for iface in keeper.interfaces()]
    if keeper.settings.role is Role.EXIT_LINKS:
        print(json.dumps([status.model_dump() for status in statuses], ensure_ascii=False))
        return 0 if statuses and all(status.fine for status in statuses) else 1
    print(json.dumps(statuses[0].model_dump(), ensure_ascii=False))
    return 0 if statuses[0].fine else 1


def strip_sum(keeper: Keeper, args: list[str]) -> int:
    """A fingerprint of what `awg setconf` would be given, to compare with `awg-quick strip`."""
    settings = keeper.settings
    if settings.role is Role.EXIT_LINKS:
        if not args or args[0] not in keeper.interfaces():
            print("strip-sum: which link?", file=sys.stderr)
            return 1
        iface = args[0]
    else:
        iface = settings.iface
    print(keeper.read(iface).fingerprint())
    return 0


async def serve(keeper: Keeper) -> None:
    """What this process does until it is told to stop: one look at the start,
    a look at an interface whenever the kernel says something about it, and a
    look at everything now and then for what the kernel does not announce."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop_signal, stop.set)
    keeper.say_start()
    logging.getLogger("apscheduler").setLevel(logging.ERROR)      # a look that takes longer than the pause between two is not news
    scheduler = AsyncIOScheduler()
    scheduler.add_job(keeper.keep_all, "interval", seconds=keeper.settings.pass_seconds, max_instances=1, coalesce=True)
    scheduler.start()
    events = asyncio.create_task(keeper.watch_links())
    first_look = asyncio.create_task(keeper.keep_all())
    try:
        await stop.wait()
    finally:
        events.cancel()
        first_look.cancel()
        scheduler.shutdown(wait=False)
    keeper.say_stop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    keeper = Keeper(get_settings())
    if argv[:1] == [Command.CHECK]:
        return asyncio.run(check(keeper))
    if argv[:1] == [Command.STRIP_SUM]:
        return strip_sum(keeper, argv[1:])
    asyncio.run(serve(keeper))
    return 0


if __name__ == "__main__":
    sys.exit(main())
