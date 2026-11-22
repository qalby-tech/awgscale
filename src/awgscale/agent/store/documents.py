"""Documents as the tables hold them: the lowest level of the store.

A record is JSON as it came. A field goes to its column only when it has
exactly the column's type; anything else (and an explicit null) is kept as it
is in `extra`, so what is read back is what was stored.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from ..errors import StoreRefused

Record = dict[str, Any]            # one stored record, as JSON
Document = list[Record]            # an ordered kind
Mapped = dict[str, Any]            # a kind that is a mapping (pauses, DNS codes)


class Kind(StrEnum):
    RULES = "rules"
    SITES = "sites"
    BLOCK = "block"
    SPACES = "spaces"
    USER_GROUPS = "ugroups"
    INVITES = "invites"
    SUSPENDED = "suspended"
    DNS = "dns"


@dataclass(frozen=True, slots=True)
class Column:
    field: str
    name: str
    type: type


@dataclass(frozen=True, slots=True)
class Table:
    """Where an ordered kind lives: its records, and the list each record carries."""

    name: str
    columns: tuple[Column, ...]
    list_field: str
    list_table: str


TABLES: Mapping[Kind, Table] = {
    Kind.RULES: Table(
        "rules", (Column("name", "name", str), Column("on", "enabled", bool), Column("route", "route", str)), "items", "rule_items"
    ),
    Kind.SITES: Table("site_groups", (Column("name", "name", str), Column("on", "enabled", bool)), "items", "site_group_items"),
    Kind.BLOCK: Table("block_groups", (Column("name", "name", str), Column("on", "enabled", bool)), "items", "block_group_items"),
    Kind.SPACES: Table("spaces", (Column("name", "name", str),), "members", "space_members"),
    Kind.USER_GROUPS: Table(
        "user_groups",
        (Column("name", "name", str), Column("space", "is_space", bool), Column("exit", "exit_pin", int), Column("filter", "filter", str)),
        "members",
        "user_group_members",
    ),
    Kind.INVITES: Table(
        "invites",
        (
            Column("hash", "token_hash", str),
            Column("group", "group_id", str),
            Column("left", "uses_left", int),
            Column("total", "uses_total", int),
            Column("expires_at", "expires_at", int),
            Column("created", "created_at", int),
        ),
        "used",
        "invite_uses",
    ),
}
SQL_TYPES: Mapping[type, str] = {str: "text", bool: "boolean", int: "bigint"}
MAPPED_KINDS = (Kind.SUSPENDED, Kind.DNS)


def schema() -> list[str]:
    """Every table of the store; made by `store-init`."""
    statements = [
        "CREATE TABLE IF NOT EXISTS meta (key text PRIMARY KEY, value text NOT NULL)",
        "CREATE TABLE IF NOT EXISTS suspended (user_name text PRIMARY KEY, since bigint NOT NULL)",
        # devices that use the DNS without the VPN: the code each one presents
        "CREATE TABLE IF NOT EXISTS dns_devices (token text PRIMARY KEY, name text NOT NULL UNIQUE, created_at bigint NOT NULL)",
    ]
    for table in TABLES.values():
        typed = "".join(f", {column.name} {SQL_TYPES[column.type]}" for column in table.columns)
        statements.append(f"CREATE TABLE IF NOT EXISTS {table.name} (id text PRIMARY KEY, pos integer NOT NULL{typed}, listed boolean NOT NULL, extra jsonb NOT NULL)")
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {table.list_table} (parent_id text NOT NULL REFERENCES {table.name}(id) ON DELETE CASCADE, "
            "pos integer NOT NULL, value text NOT NULL, PRIMARY KEY (parent_id, pos))"
        )
    return statements


@dataclass(frozen=True, slots=True)
class Row:
    """A record taken apart for its table."""

    id: str
    position: int
    typed: tuple[Any, ...]          # one value per column; None where the record's value is not of the column's type
    listed: bool                    # the record's list is a list of strings, and is kept in the list table
    extra: Record                   # everything that has no column of its own
    entries: tuple[str, ...]


def rows_of(kind: Kind, value: object) -> list[Row]:
    """A document as rows. Raises StoreRefused for what the store does not take."""
    table = TABLES[kind]
    if not isinstance(value, list):
        raise StoreRefused(f"{kind.value}: не список")
    column_types = {column.field: column.type for column in table.columns}
    rows: list[Row] = []
    seen: set[str] = set()
    for position, record in enumerate(value):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"] or record["id"] in seen:
            raise StoreRefused(f"{kind.value}: запись без своего id")
        seen.add(record["id"])
        typed = tuple(record[column.field] if column.field in record and type(record[column.field]) is column.type else None for column in table.columns)
        entries = record.get(table.list_field)
        listed = isinstance(entries, list) and all(type(entry) is str for entry in entries)
        extra = {
            field: held
            for field, held in record.items()
            if field != "id" and not (field == table.list_field and listed) and not (field in column_types and type(held) is column_types[field])
        }
        rows.append(Row(record["id"], position, typed, listed, extra, tuple(entries) if listed else ()))
    return rows


def check(kind: Kind, value: object) -> None:
    """Raises StoreRefused for what the store would refuse. Asked before anything
    outside the store is changed for a save (the blocker, the kernel)."""
    if kind is Kind.SUSPENDED:
        if not isinstance(value, dict) or not all(type(name) is str and type(since) is int for name, since in value.items()):
            raise StoreRefused("suspended: не {имя: время}")
        return
    if kind is Kind.DNS:
        fits = isinstance(value, dict) and all(
            type(token) is str and token and isinstance(device, dict) and set(device) == {"name", "created"}
            and type(device["name"]) is str and type(device["created"]) is int
            for token, device in value.items()
        )
        if not fits or len({device["name"] for device in value.values()}) != len(value):
            raise StoreRefused("dns: не {код: {name, created}} с разными именами")
        return
    rows_of(kind, value)
