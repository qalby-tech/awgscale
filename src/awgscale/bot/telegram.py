"""The Telegram Bot API, as much of it as the bot uses."""
from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from .models import Me, Update
from .settings import BotSettings

log = logging.getLogger("awgscale.bot")
# the HTTP client writes the address of every request into the log, and the token is part of the address
for chatty in ("httpx", "httpcore"):
    logging.getLogger(chatty).setLevel(logging.WARNING)

REQUEST_TIMEOUT = 30.0
POLL_MARGIN = 10.0                 # how much longer than Telegram holds a request for updates the bot waits for it
PARSE_MODE = "HTML"
ALLOWED_UPDATES = ("message", "callback_query")


class TelegramError(Exception):
    """Telegram refused, or could not be reached. Its text never carries the request's address: the token is part of it."""


class Method(StrEnum):
    GET_ME = "getMe"
    SET_MY_COMMANDS = "setMyCommands"
    GET_UPDATES = "getUpdates"
    SEND_MESSAGE = "sendMessage"
    SEND_PHOTO = "sendPhoto"
    SEND_DOCUMENT = "sendDocument"
    EDIT_MESSAGE_TEXT = "editMessageText"
    ANSWER_CALLBACK_QUERY = "answerCallbackQuery"


class Button(BaseModel):
    text: str = Field(description="What the button says")
    callback_data: str | None = Field(default=None, description="What a press brings back to the bot")
    url: str | None = Field(default=None, description="The link a press opens")


class Keyboard(BaseModel):
    """Buttons under a message."""

    inline_keyboard: list[list[Button]] = Field(description="The rows of buttons")

    def wire(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class MenuCommand(BaseModel):
    command: str = Field(description="The command, without the slash")
    description: str = Field(description="What Telegram's menu says about it")


class Attachment(BaseModel):
    name: str = Field(description="The file's name")
    content: bytes = Field(description="The file itself", repr=False)


class Telegram:
    def __init__(self, settings: BotSettings, http: httpx.AsyncClient) -> None:
        self.__settings = settings
        self.__http = http

    def __address(self, method: Method) -> str:
        return f"{self.__settings.api_url}/bot{self.__settings.token().get_secret_value()}/{method.value}"

    async def __call(self, method: Method, timeout: float = REQUEST_TIMEOUT, **request: Any) -> Any:
        """One call. `request` is httpx's own: json=…, or data=… with files=…"""
        try:
            response = await self.__http.post(self.__address(method), timeout=timeout, **request)
            answer = response.json()
        except (httpx.HTTPError, OSError, ValueError) as e:
            raise TelegramError(f"{method.value}: {type(e).__name__}") from None
        if not isinstance(answer, dict) or not answer.get("ok"):
            description = answer.get("description") if isinstance(answer, dict) else None
            raise TelegramError(f"{method.value}: {description or response.status_code}")
        return answer.get("result")

    @staticmethod
    def __text_fields(keyboard: Keyboard | None) -> dict[str, Any]:
        """What every text the bot sends carries: HTML, no link previews, and the buttons when there are any."""
        fields: dict[str, Any] = {"parse_mode": PARSE_MODE, "link_preview_options": {"is_disabled": True}}
        if keyboard is not None:
            fields["reply_markup"] = keyboard.wire()
        return fields

    async def get_me(self) -> Me:
        return Me.model_validate(await self.__call(Method.GET_ME))

    async def set_my_commands(self, commands: list[MenuCommand]) -> None:
        await self.__call(Method.SET_MY_COMMANDS, json={"commands": [command.model_dump() for command in commands]})

    async def get_updates(self, offset: int | None) -> list[Update]:
        """Waits for what was written to the bot; Telegram holds the request open until there is something."""
        hold = self.__settings.poll_seconds
        request: dict[str, Any] = {"timeout": hold, "allowed_updates": list(ALLOWED_UPDATES)}
        if offset is not None:
            request["offset"] = offset
        result = await self.__call(Method.GET_UPDATES, timeout=hold + POLL_MARGIN, json=request)
        return [update for update in map(self.__update, result or []) if update is not None]

    @staticmethod
    def __update(raw: object) -> Update | None:
        """One update as Telegram sent it. An update the bot cannot read still has its
        number — it comes back empty, so it is passed over instead of being sent again for ever."""
        try:
            return Update.model_validate(raw)
        except ValidationError:
            number = raw.get("update_id") if isinstance(raw, dict) else None
            if not isinstance(number, int):
                return None
            log.warning("update %s is not one the bot can read", number)
            return Update(update_id=number)

    async def send_message(self, chat_id: int, text: str, keyboard: Keyboard | None = None) -> None:
        await self.__call(Method.SEND_MESSAGE, json={"chat_id": chat_id, "text": text, **self.__text_fields(keyboard)})

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        await self.__call(Method.EDIT_MESSAGE_TEXT, json={"chat_id": chat_id, "message_id": message_id, "text": text, **self.__text_fields(None)})

    async def answer_callback_query(self, callback_id: str) -> None:
        await self.__call(Method.ANSWER_CALLBACK_QUERY, json={"callback_query_id": callback_id})

    async def __send_file(self, method: Method, field: str, chat_id: int, attachment: Attachment, caption: str) -> None:
        await self.__call(
            method,
            data={"chat_id": str(chat_id), "caption": caption, "parse_mode": PARSE_MODE},
            files={field: (attachment.name, attachment.content)},
        )

    async def send_photo(self, chat_id: int, photo: Attachment, caption: str) -> None:
        await self.__send_file(Method.SEND_PHOTO, "photo", chat_id, photo, caption)

    async def send_document(self, chat_id: int, document: Attachment, caption: str) -> None:
        await self.__send_file(Method.SEND_DOCUMENT, "document", chat_id, document, caption)


class Admin:
    """The admin's chat: where everything the bot says by itself goes."""

    def __init__(self, settings: BotSettings, telegram: Telegram) -> None:
        self.__settings = settings
        self.__telegram = telegram

    @property
    def id(self) -> int:
        return self.__settings.admin_id

    async def notify(self, text: str, keyboard: Keyboard | None = None) -> bool:
        """Says it to the admin. → False when it could not be delivered (said in the log, never raised)."""
        try:
            await self.__telegram.send_message(self.id, text, keyboard)
        except TelegramError as e:
            log.warning("cannot message the admin: %s", e)
            return False
        return True
