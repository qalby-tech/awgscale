"""What blocks a name, read from the blocker's own answer."""
from __future__ import annotations

import urllib.parse
from typing import Any

from ..ports import BlockGroups, Blocker
from .models import Blocked, BlockedKind

# Names shown in the panel for the blocker's ready-made lists
LIST_TITLES = {
    "https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt": "Реклама",
    "https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt": "AdAway",
}
FILTERED = "Filtered"
SAFE_BROWSING = "FilteredSafeBrowsing"
CHECK_TIMEOUT = 5.0


def rule_of(entry: str) -> str:
    """An entry of the admin's block group, as the rule the blocker holds."""
    return "||" + entry + "^"


async def blocked_by(blocker: Blocker, block_groups: BlockGroups, host: str) -> Blocked | None:
    """What blocks this name: a group of the admin's, a block list, the
    dangerous-sites check — or None. Raises whatever the blocker or the store raises."""
    verdict = await blocker.get("filtering/check_host?name=" + urllib.parse.quote(host), CHECK_TIMEOUT)
    if not str(verdict.get("reason", "")).startswith(FILTERED):
        return None
    rule: dict[str, Any] = (verdict.get("rules") or [{}])[0]
    text = rule.get("text") or verdict.get("rule") or ""
    found = Blocked(rule=text)
    for group in await block_groups.read():
        if any(rule_of(entry) == text for entry in group.get("items", [])):
            found = found.model_copy(update={"gid": group.get("id"), "list": group.get("name")})
    status = await blocker.get("filtering/status", CHECK_TIMEOUT)
    for block_list in status.get("filters") or []:
        if block_list.get("id") == rule.get("filter_list_id"):
            title = LIST_TITLES.get(block_list.get("url"), block_list.get("name"))
            found = found.model_copy(update={"kind": BlockedKind.LIST, "id": block_list.get("id"), "list": title})
    if str(verdict.get("reason")) == SAFE_BROWSING:
        found = found.model_copy(update={"kind": BlockedKind.SAFE, "list": "Опасные сайты", "rule": ""})
    return found
