"""AdGuard's query log, read from its end. The log keeps months; a day of it is what is counted."""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

BLOCK_BYTES = 4 << 20
LIMIT_BYTES = 128 << 20
EPOCH = datetime.fromtimestamp(0, UTC)

# The moment of a log line:   {"T":"2026-11-16T10:20:30.123456789+03:00","QH":"example.com", …}
MOMENT_RE = re.compile(
    rb"""
    "T":"(?P<moment>[^"]+)"      # "T":"2026-11-16T10:20:30.123456789+03:00"
    """,
    re.VERBOSE,
)
# nanoseconds → microseconds: datetime takes six digits at most
BEYOND_MICROSECONDS_RE = re.compile(
    r"""
    (?P<micro>\.\d{6})     # .123456
    \d+                    # 789
    """,
    re.VERBOSE,
)


def moment_of(raw: bytes) -> datetime:
    """When a line of the log was written; the beginning of time for a line that does not say."""
    found = MOMENT_RE.search(raw)
    if not found:
        return EPOCH
    text = BEYOND_MICROSECONDS_RE.sub(r"\g<micro>", found["moment"].decode()).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return EPOCH
    return moment if moment.tzinfo is not None else moment.astimezone()


def tail(path: Path, cutoff: datetime, block: int = BLOCK_BYTES, limit: int = LIMIT_BYTES) -> tuple[list[bytes], bool]:
    """Lines of the log from `cutoff` on: read from the end, a block at a time,
    until a line older than the cutoff. → (lines, reached an older one)"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], False
    chunks: list[list[bytes]] = []
    position = size
    carry = b""
    older = False
    with open(path, "rb") as handle:
        while position > 0 and size - position < limit and not older:
            count = min(block, position)
            position -= count
            handle.seek(position)
            lines = (handle.read(count) + carry).split(b"\n")
            carry = lines.pop(0) if position > 0 else b""        # the first piece may be half a line
            chunks.append(lines)
            for raw in lines:
                if raw.strip():
                    older = moment_of(raw) < cutoff
                    break
    return [line for chunk in reversed(chunks) for line in chunk], older
