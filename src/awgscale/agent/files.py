"""Files the agent compiles for others to read: written whole or not at all."""
from __future__ import annotations

import os
import threading
from pathlib import Path

PRIVATE_MODE = 0o600


def write_file(path: Path, text: str, mode: int = PRIVATE_MODE) -> None:
    """Written beside and moved into place. The temporary name is one's own: two commands may write the same file at once."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.replace(temporary, path)


def read_text(path: Path) -> str | None:
    """The file's text; None when it is not there or cannot be read."""
    try:
        return path.read_text()
    except OSError:
        return None
