"""The agent's unix socket as its callers use it: one line of JSON in, the last line of its answer back.

The line is the agent's own protocol (awgscale.agent.protocol.SocketRequest):
the command and its arguments, who asks, and — for one command only — a secret
beside the arguments. What the answer means, the timeouts around the whole
exchange and the words for an agent that does not answer stay with each caller."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Sequence


async def exchange(
    socket_path: Path,
    args: Sequence[str],
    by: str,
    secret: str | None = None,
    connect_timeout: float | None = None,
    read_timeout: float | None = None,
) -> str:
    """→ the last line of the agent's answer, as text (JSON to be read by the
    caller). Raises OSError, TimeoutError, or IndexError for an empty answer."""
    request: dict[str, object] = {"args": list(args), "by": by}
    if secret:
        request["secret"] = secret
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path)), connect_timeout)
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        writer.write_eof()
        answer = await asyncio.wait_for(reader.read(), read_timeout)
    finally:
        writer.close()
    return answer.decode().strip().splitlines()[-1]
