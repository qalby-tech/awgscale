"""What the admin can ask the bot for, and what it answers."""
from __future__ import annotations

import base64
import logging
import re
import urllib.parse
from enum import StrEnum
from typing import Awaitable, Callable

from .agent import Agent, AgentError
from .format import escaped
from .models import CallbackQuery, Group, InviteRequest, Message
from .settings import BotSettings
from .stats import Sampler
from .telegram import Admin, Attachment, Button, Keyboard, MenuCommand, Telegram
from .texts import USERS_SHOWN, status_text, users_text

log = logging.getLogger("awgscale.bot")

# A user's name as the admin types it after a command:   alice   phone-7
NAME_RE = re.compile(
    r"""
    ^[A-Za-z0-9_-]{1,32}$
    """,
    re.VERBOSE,
)
# A term:   12h   7d   4w   — hours, days or weeks, 1…999 of them
TERM_RE = re.compile(
    r"""
    ^[1-9][0-9]{0,2}      # 12
    [hdw]$                # h
    """,
    re.VERBOSE,
)
COMMAND_PREFIX = "/"
DEFAULT_INVITE_USES = 1
DEFAULT_INVITE_DAYS = 7
MAX_INVITE_USES = 10
MAX_INVITE_DAYS = 30
MAX_SEARCH = 64


class Command(StrEnum):
    START = "start"
    HELP = "help"
    STATUS = "status"
    USERS = "users"
    REPORT = "report"
    ADD = "add"
    INVITE = "invite"
    KEY = "key"
    DEL = "del"


class Callback(StrEnum):
    """What the buttons under the bot's messages carry."""

    CANCEL = "cancel"
    DELETE = "del"


DELETE_PREFIX = Callback.DELETE + ":"

MENU = [
    MenuCommand(command=Command.STATUS, description="Состояние каскада"),
    MenuCommand(command=Command.USERS, description="Пользователи"),
    MenuCommand(command=Command.ADD, description="Новый пользователь: /add имя [срок]"),
    MenuCommand(command=Command.KEY, description="Прислать код: /key имя"),
    MenuCommand(command=Command.DEL, description="Удалить: /del имя"),
    MenuCommand(command=Command.INVITE, description="Приглашение: /invite [устройств] [дней] [группа]"),
    MenuCommand(command=Command.REPORT, description="Сводка за текущие сутки"),
]


def invite_request(text: str) -> tuple[int, int, str | None] | None:
    """`/invite [устройств] [дней] [группа]` → (uses, days, group name | None), or None when it cannot be read."""
    words = text.split()
    numbers: list[int] = []
    while words and words[0].isdigit() and len(numbers) < 2:
        numbers.append(int(words.pop(0)))
    uses = numbers[0] if numbers else DEFAULT_INVITE_USES
    days = numbers[1] if len(numbers) > 1 else DEFAULT_INVITE_DAYS
    if not (1 <= uses <= MAX_INVITE_USES and 1 <= days <= MAX_INVITE_DAYS):
        return None
    return uses, days, " ".join(words) or None


class Commands:
    def __init__(self, settings: BotSettings, telegram: Telegram, admin: Admin, agent: Agent, sampler: Sampler) -> None:
        self.__settings = settings
        self.__telegram = telegram
        self.__admin = admin
        self.__agent = agent
        self.__sampler = sampler
        self.__username: str | None = None
        self.__handlers: dict[Command, Callable[[Message, str], Awaitable[None]]] = {
            Command.START: self.__help,
            Command.HELP: self.__help,
            Command.STATUS: self.__status,
            Command.USERS: self.__users,
            Command.REPORT: self.__report,
            Command.ADD: self.__add,
            Command.INVITE: self.__invite,
            Command.KEY: self.__key,
            Command.DEL: self.__delete,
        }

    def named(self, username: str | None) -> None:
        """The bot's own name, once Telegram has told it: `/status@name` is then for us only when the name is ours."""
        self.__username = username

    async def __answer(self, message: Message, text: str, keyboard: Keyboard | None = None) -> None:
        await self.__telegram.send_message(message.chat.id, text, keyboard)

    def __command_of(self, text: str) -> tuple[Command, str] | None:
        """`/add name 7d` → (Command.ADD, "name 7d"); None for what is not one of the commands."""
        words = text.split(maxsplit=1)
        head = words[0]
        arguments = words[1] if len(words) > 1 else ""
        name, _, mention = head[len(COMMAND_PREFIX):].partition("@")
        if mention and (self.__username is None or mention.lower() != self.__username.lower()):
            return None
        if name not in tuple(Command):
            return None
        return Command(name), arguments

    # ── messages ─────────────────────────────────────────────────────────────

    async def on_message(self, message: Message) -> None:
        """Admin only, private chat only. Anything else is dropped without a word."""
        admin_id = self.__settings.admin_id
        if message.from_user is None or message.from_user.id != admin_id or message.chat.id != admin_id:
            return
        text = message.text or message.caption          # a command may also be written under a picture
        if text is None or not text.startswith(COMMAND_PREFIX):
            return
        command = self.__command_of(text)
        if command is None:
            if message.text is not None:
                await self.__answer(message, "Не знаю такой команды. /help")
            return
        await self.__handlers[command[0]](message, command[1])

    async def __help(self, message: Message, arguments: str) -> None:
        await self.__answer(
            message,
            "<b>Команды</b>\n/status — состояние каскада\n/users — пользователи\n"
            "/add имя [срок] — новый пользователь, срок например 7d\n/key имя — прислать код ещё раз\n"
            "/del имя — удалить пользователя\n/invite [устройств] [дней] [группа] — ссылка-приглашение, её можно переслать\n"
            "/report — сводка за текущие сутки\n\n"
            f"Сам пришлю: оповещения о сбоях и сводку каждый день в {self.__settings.report_hour}:00 по Москве.",
        )

    async def __status(self, message: Message, arguments: str) -> None:
        try:
            state = await self.__agent.state()
        except AgentError as e:
            await self.__answer(message, "Нет связи с сервером: " + escaped(e.reason("")))
            return
        await self.__answer(message, status_text(state))

    async def __users(self, message: Message, arguments: str) -> None:
        """A page of users; `/users часть имени` finds the others."""
        search = arguments.strip()[:MAX_SEARCH]
        try:
            state = await self.__agent.state()
            page = await self.__agent.clients(search=search, limit=USERS_SHOWN)
        except AgentError as e:
            await self.__answer(message, "Нет связи с сервером: " + escaped(e.reason("")))
            return
        await self.__answer(message, users_text(state, page, self.__settings.local_zone, search))

    async def __report(self, message: Message, arguments: str) -> None:
        try:
            state = await self.__agent.state()
            text = await self.__sampler.report(state, final=False)
        except AgentError as e:
            await self.__answer(message, "Нет связи с сервером: " + escaped(e.reason("")))
            return
        await self.__answer(message, text)

    async def __send_key(self, name: str) -> None:
        """The user's QR, the vpn:// string and the config file, to the admin."""
        try:
            key = await self.__agent.key(name)
        except AgentError as e:
            await self.__admin.notify("Не получилось: " + escaped(e.reason("ошибка")))
            return
        if key.uri_png:
            picture = Attachment(name=f"{name}.png", content=base64.b64decode(key.uri_png))
            await self.__telegram.send_photo(self.__admin.id, picture, f"{escaped(name)}: QR для приложения AmneziaVPN")
        if key.uri:
            await self.__admin.notify(f"Строка для компьютера (AmneziaVPN → «Вставить ключ»):\n<code>{escaped(key.uri)}</code>")
        config = Attachment(name=f"{name}.conf", content=key.conf.encode())
        await self.__telegram.send_document(self.__admin.id, config, "Файл для AmneziaWG и WG Tunnel")

    async def __add(self, message: Message, arguments: str) -> None:
        words = arguments.split()
        if not words or not NAME_RE.match(words[0]):
            await self.__answer(message, "Формат: /add имя [срок]\nИмя: латиница, цифры, _ и -, до 32 символов. Срок: 12h, 7d, 4w.")
            return
        name = words[0]
        term = words[1] if len(words) > 1 else None
        if term is not None and not TERM_RE.match(term):
            await self.__answer(message, "Срок указывается так: 12h, 7d, 30d, 4w")
            return
        try:
            await self.__agent.add(name, term)
        except AgentError as e:
            await self.__answer(message, "Не получилось создать: " + escaped(e.reason("ошибка")))
            return
        await self.__answer(message, f"Создан пользователь <code>{escaped(name)}</code>" + (f", срок {escaped(term)}" if term is not None else ""))
        await self.__send_key(name)

    async def __group(self, message: Message, group_name: str) -> Group | None:
        """The group with this name; when there is none, says which groups there are (and → None)."""
        try:
            groups = await self.__agent.groups()
        except AgentError:
            groups = []
        found = [group for group in groups if group.name.casefold() == group_name.casefold()]
        if not found:
            names = ", ".join(escaped(group.name) for group in groups) or "групп нет"
            await self.__answer(message, f"Группы «{escaped(group_name)}» нет. Есть: {names}")
            return None
        return found[0]

    async def __invite(self, message: Message, arguments: str) -> None:
        domain = self.__settings.domain
        if not domain:
            await self.__answer(message, "Не настроен адрес сервера")
            return
        request = invite_request(arguments)
        if request is None:
            await self.__answer(message, "Формат: /invite [устройств 1–10] [дней 1–30] [группа]\nНапример: /invite 2 7 Семья")
            return
        uses, days, group_name = request
        group_id: str | None = None
        if group_name:
            group = await self.__group(message, group_name)
            if group is None:
                return
            group_id = group.id
        try:
            secret = await self.__agent.invite(InviteRequest(group=group_id, uses=uses, days=days))
        except AgentError as e:
            await self.__answer(message, "Не получилось: " + escaped(e.reason("ошибка")))
            return
        link = f"https://{domain}/i/{secret}"
        share = "https://t.me/share/url?" + urllib.parse.urlencode({"url": link, "text": "Приглашение в VPN: откройте ссылку и следуйте шагам"})
        where = " в группу «" + escaped(group_name) + "»" if group_name else ""
        await self.__answer(
            message,
            f"Приглашение{where}: устройств {uses}, действует {days} дн.\n<code>{escaped(link)}</code>",
            Keyboard(inline_keyboard=[[Button(text="Переслать", url=share)]]),
        )

    async def __key(self, message: Message, arguments: str) -> None:
        name = arguments.strip()
        if not NAME_RE.match(name):
            await self.__answer(message, "Формат: /key имя")
            return
        await self.__send_key(name)

    async def __delete(self, message: Message, arguments: str) -> None:
        name = arguments.strip()
        if not NAME_RE.match(name):
            await self.__answer(message, "Формат: /del имя")
            return
        buttons = [Button(text="Удалить", callback_data=DELETE_PREFIX + name), Button(text="Отмена", callback_data=Callback.CANCEL)]
        await self.__answer(
            message,
            f"Удалить пользователя <code>{escaped(name)}</code>? Его ключ перестанет работать сразу.",
            Keyboard(inline_keyboard=[buttons]),
        )

    # ── buttons ──────────────────────────────────────────────────────────────

    async def on_callback(self, callback: CallbackQuery) -> None:
        if callback.from_user.id != self.__settings.admin_id:
            return
        data = callback.data or ""
        if data == Callback.CANCEL:
            await self.__telegram.answer_callback_query(callback.id)
            await self.__edit(callback, "Отменено.")
        elif data.startswith(DELETE_PREFIX):
            await self.__telegram.answer_callback_query(callback.id)
            await self.__delete_confirmed(callback, data[len(DELETE_PREFIX):])

    async def __edit(self, callback: CallbackQuery, text: str) -> None:
        if callback.message is None:
            return
        await self.__telegram.edit_message_text(callback.message.chat.id, callback.message.message_id, text)

    async def __delete_confirmed(self, callback: CallbackQuery, name: str) -> None:
        if not NAME_RE.match(name):
            return
        try:
            await self.__agent.remove(name)
        except AgentError as e:
            await self.__edit(callback, "Не получилось удалить: " + escaped(e.reason("ошибка")))
            return
        await self.__edit(callback, f"Пользователь <code>{escaped(name)}</code> удалён.")
