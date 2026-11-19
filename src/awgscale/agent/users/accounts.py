"""Making a user and removing users: the users service does it, the agent keeps
everything it stores about them in step."""
from __future__ import annotations

from awgscale.users.models import Status

from ..commands import Add, Remove
from ..errors import Refused
from ..ports import PIN_TIMEOUT, AccountsGate, LeavingNotes, Readiness, UsersDirectory, WatchGate
from .forgetting import Forgetting
from .models import UserAdded, UsersRemoved

SHOWN_REASON = 200
GONE = (Status.REMOVED.value, Status.NOT_FOUND.value)


class AccountsService:
    def __init__(self, store: Readiness, directory: UsersDirectory, users: AccountsGate, forgetting: Forgetting, seen: LeavingNotes, watch: WatchGate) -> None:
        self.__store = store
        self.__directory = directory
        self.__users = users
        self.__forgetting = forgetting
        self.__seen = seen
        self.__watch = watch

    async def add(self, request: Add) -> UserAdded:
        name = request.name
        await self.__store.ping()                      # no user is made for a change the store could not follow
        if any(user.name == name for user in await self.__directory.list(force=True)):
            raise Refused("пользователь с таким именем уже есть")
        # Whoever had this name before is gone — removed here, or by the users
        # service's own expiry job, which this agent never sees. The new user must
        # not inherit their place in a space or a group.
        await self.__forgetting.forget([name])
        try:
            await self.__seen.back(name)
        except Exception:
            pass
        made = await self.__users.add(name, request.expires)
        await self.__directory.list(force=True)
        await self.__watch.pin(PIN_TIMEOUT)            # pin the new user to an exit right away
        if made is None:
            raise Refused("не удалось создать пользователя: сервис пользователей не отвечает")
        if not made.ok:
            raise Refused("не удалось создать пользователя: " + str(made.error or "ошибка")[:SHOWN_REASON])
        return UserAdded(result=made.answer)

    async def remove(self, request: Remove) -> UsersRemoved:
        """Revoke one user or several at once (a whole group): one call of the users service."""
        names = list(dict.fromkeys(request.names))
        await self.__store.ping()                      # no user is removed for a change the store could not follow
        try:
            await self.__seen.leaving(names)
        except Exception:
            pass
        answered = await self.__users.remove(names)
        await self.__directory.list(force=True)
        await self.__watch.pin(PIN_TIMEOUT)
        results = answered.results if answered is not None else ()
        removed = [result.name for result in results if result.status == Status.REMOVED.value]
        gone = {result.name for result in results if result.status in GONE}
        await self.__forgetting.forget([name for name in names if name in gone])
        left = [name for name in names if name not in removed]
        if answered is None or left:
            if not removed:
                reason = next((str(result.error) for result in results if result.error), None) if answered is not None else "сервис пользователей не отвечает"
                raise Refused("не удалось удалить" + (": " + reason[:SHOWN_REASON] if reason else ""))
            assert answered is not None
            raise Refused("не удалены: " + ", ".join(left), removed=removed, result=answered.answer)
        return UsersRemoved(removed=removed, result=answered.answer)
