"""Moving the settings into the database, once, and showing what it holds.

`store-init`: create the tables, move the settings from the files into them,
read them back and compare. The mark that lets the agent use the store is set
in the same transaction, only when every kind came back as it went in. Run
again, it changes nothing.

An empty database is never quietly taken for a new one. If the settings were
moved before (the note on the host says so) and the database has no mark, it
was lost: that is a restore from a dump, not an import. And with no files to
move at all, nothing is initialised unless asked for by name (`store-init
--fresh`, a new server).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..cascade import conf
from ..cascade.models import CascadeSettings, checked
from ..cascade.store import MOVED_KEY as CASCADE_MOVED_KEY, SETTINGS_KEY as CASCADE_KEY, StoredCascade
from ..errors import Refused, StoreDown
from ..files import write_file
from ..rules.store import RULES_MOVED_KEY, ensure_moved, reconcile
from ..seen.store import MOVED_KEY as SEEN_MOVED_KEY, NOTES_KEY, StoredNotes, text_of
from ..shell import StoreExport, StoreInit
from ..sites.groups import AUTOMATIC_TELEGRAM
from ..sites.routing import listed_lines
from .documents import MAPPED_KINDS, Kind, Record
from .store import DNS_MOVED_KEY, READY_KEY, Store, StoreTransaction, same

FILES_VERSION = 1
FLAT_SITES_ID = "000000"
FLAT_SITES_NAME = "Без группы"
ARCHIVE_DIR = "archive"


@dataclass(frozen=True, slots=True)
class OldFile:
    """Where a kind used to live as a file: read once by `store-init`, never after."""

    path: Path
    key: str


class Moved(BaseModel):
    """The answer of store-init."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    already: bool = Field(default=False, description="The settings were in the store before this run")
    added: list[str] = Field(default_factory=list, description="The kinds that joined the store later and were moved by this run")
    counts: dict[str, int] = Field(default_factory=dict, description="How many records of each kind the first move brought in")

    def wire(self) -> dict[str, Any]:
        if self.already:
            return {"ok": True, "already": True, **({"moved": self.added} if self.added else {})}
        return {"ok": True, "moved": self.counts}


class Exported(BaseModel):
    """The answer of store-export."""

    ok: bool = Field(default=True, description="Always true: a refusal has another shape")
    files: dict[str, dict[str, Any]] = Field(description="Every kind, under the name and in the shape of the file it used to be")

    def wire(self) -> dict[str, Any]:
        return self.model_dump()


def stamp() -> str:
    """The moment a mark is set, as the marks have always held it: whole Unix seconds as text."""
    return str(int(time.time()))


class StoreMoving:
    def __init__(self, store: Store, cascade_dir: Path, dns_tokens_file: Path) -> None:
        self.__store = store
        self.__files: dict[Kind, OldFile] = {
            Kind.SITES: OldFile(cascade_dir / "groups.json", "groups"),
            Kind.BLOCK: OldFile(cascade_dir / "block.json", "groups"),
            Kind.SPACES: OldFile(cascade_dir / "spaces.json", "spaces"),
            Kind.USER_GROUPS: OldFile(cascade_dir / "usergroups.json", "groups"),
            Kind.SUSPENDED: OldFile(cascade_dir / "suspended.json", "names"),
            Kind.INVITES: OldFile(cascade_dir / "invites.json", "invites"),
            Kind.DNS: OldFile(dns_tokens_file, "tokens"),
        }
        self.__conf_file = cascade_dir / "cascade.conf"
        self.__flat_sites_file = cascade_dir / "domains-exit.txt"
        self.__seen_file = cascade_dir / ".seen.json"
        self.__moved_note = cascade_dir / ".store-moved"        # left on the host once the settings were moved into the database
        self.__cascade = StoredCascade(store)
        self.__notes = StoredNotes(store)

    # ── what the files hold ──────────────────────────────────────────────────

    def __documents(self) -> dict[Kind, Any]:
        """What the files hold, kind by kind, as it is to be stored. A kind whose
        file is missing is empty; a file that is there but cannot be understood
        stops the import."""
        documents: dict[Kind, Any] = {}
        for kind, file in self.__files.items():
            empty: Any = {} if kind in MAPPED_KINDS else []
            if not file.path.exists():
                # the sites before they were grouped: the flat list as one group, automatic networks on
                flat = [{"id": FLAT_SITES_ID, "name": FLAT_SITES_NAME, "on": True, "items": listed_lines(self.__flat_sites_file), "auto": AUTOMATIC_TELEGRAM}]
                documents[kind] = flat if kind is Kind.SITES else empty
                continue
            data = json.loads(file.path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get(file.key), type(empty)):
                raise ValueError(f"{file.path}: не тот вид")
            documents[kind] = data[file.key]
        return documents

    def __cascade_of_file(self) -> CascadeSettings | None:
        """The settings as the file on the host has them, or None without a file."""
        if not self.__conf_file.exists():
            return None
        try:
            return checked(conf.parse(self.__conf_file.read_text()).wire())
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(f"{self.__conf_file}: {e}") from None

    def __seen_of_file(self) -> Record:
        """What the journal last saw, from the file it used to be kept in ({} without one).
        A file that is there but cannot be understood stops the import."""
        if not self.__seen_file.exists():
            return {}
        data = json.loads(self.__seen_file.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{self.__seen_file}: не тот вид")
        return data

    # ── moving ───────────────────────────────────────────────────────────────

    async def __move_seen(self, tx: StoreTransaction, notes: Record) -> None:
        await tx.set_meta(NOTES_KEY, text_of(notes))
        await tx.mark(SEEN_MOVED_KEY, stamp())

    async def __later_kinds(self, tx: StoreTransaction) -> list[str]:
        """A kind that joined the store after the first move is moved now, once."""
        added: list[str] = []
        tx.trust()
        if not await tx.marked(DNS_MOVED_KEY):
            codes = self.__documents()[Kind.DNS]
            tx.trust_dns()
            await tx.put(Kind.DNS, codes)
            await tx.mark(DNS_MOVED_KEY, stamp())
            if not same(await tx.get(Kind.DNS), codes):
                raise ValueError("коды DNS после записи читаются не так, как в файле")
            added.append(Kind.DNS.value)
        if not await tx.marked(SEEN_MOVED_KEY):
            notes = self.__seen_of_file()
            await self.__move_seen(tx, notes)
            if await self.__notes.load() != notes:
                raise ValueError("наблюдения журнала после записи читаются не так, как в файле")
            added.append(NOTES_KEY)
        if await reconcile(tx):
            added.append(Kind.RULES.value + ": собраны заново из групп сайтов и блокировки (их меняла прежняя версия)")
        if not await tx.marked(RULES_MOVED_KEY):
            rules = await ensure_moved(tx)
            if [rule.get("id") for rule in rules] != [rule.get("id") for rule in await tx.get(Kind.RULES)]:
                raise ValueError("правила после записи читаются не так, как собраны")
            added.append(Kind.RULES.value)
        if not await tx.marked(CASCADE_MOVED_KEY):
            settings = self.__cascade_of_file()
            if settings is not None:                 # a server without the file has nothing to move yet
                await self.__cascade.save(settings)
                if await self.__cascade.load() != settings:
                    raise ValueError("настройки каскада после записи читаются не так, как в файле")
                added.append(CASCADE_KEY)
        return added

    async def __first_move(self, tx: StoreTransaction, fresh: bool) -> dict[str, int]:
        if self.__moved_note.exists():
            raise ValueError("настройки уже были перенесены в базу, а база сейчас пуста — её нужно восстановить из копии (/opt/vpn-stack/db-backup), а не заполнять заново")
        if not fresh and not any(file.path.exists() for file in self.__files.values()):
            raise ValueError("файлов с настройками нет: переносить нечего. Новый сервер — store-init --fresh")
        documents = self.__documents()
        tx.trust()
        tx.trust_dns()
        for kind, document in documents.items():
            await tx.put(kind, document)
        await tx.mark(DNS_MOVED_KEY, stamp())
        await self.__move_seen(tx, self.__seen_of_file())
        settings = self.__cascade_of_file()
        if settings is not None:
            await self.__cascade.save(settings)
        differ = [kind.value for kind, document in documents.items() if not same(document, await tx.get(kind))]
        if differ:
            raise ValueError("после записи читается не то, что было в файлах: " + ", ".join(differ))
        await tx.mark(READY_KEY, stamp())
        return {kind.value: len(document) for kind, document in documents.items()}

    def __put_seen_file_away(self) -> None:
        """Moved just now: the file is put away, not left to be taken for the truth."""
        aside = self.__seen_file.parent / ARCHIVE_DIR
        aside.mkdir(parents=True, exist_ok=True)
        os.replace(self.__seen_file, aside / (self.__seen_file.name.lstrip(".") + ".until-" + time.strftime("%Y-%m-%d", time.gmtime())))

    async def __init(self, fresh: bool) -> Moved:
        added: list[str] = []
        counts: dict[str, int] = {}
        async with self.__store.transaction() as tx:      # the tables, the import and the mark: all of it or none of it
            await tx.create_schema()
            already = await tx.marked(READY_KEY)
            if already:
                added = await self.__later_kinds(tx)
            else:
                counts = await self.__first_move(tx, fresh)
                counts[Kind.RULES.value] = len(await ensure_moved(tx))
        if not self.__moved_note.exists():
            write_file(self.__moved_note, stamp() + "\n")
        try:                                              # the file on the host is from now on the compiled copy
            conf.compile_to(self.__conf_file, await self.__cascade.load())
        except StoreDown:
            pass
        if self.__seen_file.exists() and (NOTES_KEY in added or not already):
            await asyncio.to_thread(self.__put_seen_file_away)
        return Moved(already=already, added=added, counts=counts)

    async def init(self, request: StoreInit) -> Moved:
        try:
            return await self.__init(request.fresh is not None)
        except (StoreDown, ValueError, OSError) as e:
            raise Refused(f"перенос не выполнен: {e}") from None

    async def export(self, _request: StoreExport) -> Exported:
        """Everything the store holds, in the shape of the old files (a way back, and a backup a person can read)."""
        try:
            async with self.__store.transaction() as tx:
                documents = {kind: await tx.get(kind) for kind in self.__files}
        except StoreDown as e:
            raise Refused(f"Хранилище настроек недоступно: {e}") from None
        return Exported(files={file.path.name: {"v": FILES_VERSION, file.key: documents[kind]} for kind, file in self.__files.items()})
