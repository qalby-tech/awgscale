"""Listens to Telegram: every message and every pressed button is handed to the commands as it comes."""
from __future__ import annotations

import asyncio
import logging

from .commands import Commands
from .models import Update
from .settings import BotSettings
from .telegram import Telegram, TelegramError

log = logging.getLogger("awgscale.bot")


class Listener:
    def __init__(self, settings: BotSettings, telegram: Telegram, commands: Commands) -> None:
        self.__settings = settings
        self.__telegram = telegram
        self.__commands = commands
        self.__next_update: int | None = None
        self.__running: set[asyncio.Task[None]] = set()

    async def __handle(self, update: Update) -> None:
        try:
            if update.message is not None:
                await self.__commands.on_message(update.message)
            elif update.callback_query is not None:
                await self.__commands.on_callback(update.callback_query)
        except Exception:
            log.exception("update %s", update.update_id)

    def __start(self, update: Update) -> None:
        """Each update is handled on its own: a slow command does not hold the others."""
        task = asyncio.create_task(self.__handle(update))
        self.__running.add(task)
        task.add_done_callback(self.__running.discard)

    async def listen(self) -> None:
        """For as long as the process lives. Telegram holds each request open until
        there is something to say, so nothing is polled; a failure is retried."""
        while True:
            try:
                updates = await self.__telegram.get_updates(self.__next_update)
            except TelegramError as e:
                log.warning("cannot ask Telegram for updates: %s", e)
                await asyncio.sleep(self.__settings.retry_seconds)
                continue
            for update in updates:
                self.__next_update = update.update_id + 1
                self.__start(update)
