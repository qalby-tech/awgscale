"""What the watch service loads for the spaces: for every enabled space — and
every group of users that is switched to "space" — the tunnel address AND the
public key of each member. The key is what makes membership follow the user:
if the address is later given to someone else, the pair no longer matches and
the watch service leaves it out."""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from awgscale.core.net import HOST_SUFFIX
from awgscale.core.patterns import KEY_RE

from ..store.documents import Record
from ..labels import ID_RE

# A member of a space's set:   172.16.17.5
ADDRESS_RE = re.compile(
    r"""
    ^\d{1,3}(?:\.\d{1,3}){3}\Z
    """,
    re.VERBOSE,
)
SET_PREFIX = "sp_"
GROUP_MARK = "g"                   # a group's set is sp_g<id>: seven characters, so it can never be a space's (six)
MIN_MEMBERS = 2                    # one device alone has nobody to reach


def keys_by_address(allowed_ips: str) -> dict[str, str]:
    """The output of `awg show <if> allowed-ips` → {tunnel address: public key}."""
    keys: dict[str, str] = {}
    for line in allowed_ips.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not KEY_RE.match(parts[0]):
            continue
        for allowed in parts[1].split():
            address = allowed.removesuffix(HOST_SUFFIX)
            if allowed.endswith(HOST_SUFFIX) and ADDRESS_RE.match(address):
                keys[address] = parts[0]
    return keys


def _has_id(record: Record) -> bool:
    return bool(ID_RE.match(str(record.get("id", ""))))


def sets_text(spaces: Sequence[Record], groups: Sequence[Record], addresses: Mapping[object, object], keys: Mapping[object, str]) -> str:
    """The file: one line per set that has at least two members with a known address and key."""
    sets = [(space["id"], space.get("members", [])) for space in spaces if space.get("on") and _has_id(space)]
    sets += [(GROUP_MARK + group["id"], group.get("members", [])) for group in groups if group.get("space") and _has_id(group)]
    lines = []
    for set_id, members in sets:
        pairs = [f"{addresses[member]},{keys[addresses[member]]}" for member in members if addresses.get(member) in keys]
        if len(pairs) >= MIN_MEMBERS:
            lines.append(f"{SET_PREFIX}{set_id} " + " ".join(pairs))
    return "".join(line + "\n" for line in lines)
