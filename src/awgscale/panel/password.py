"""The admin's password: a file only root reads, made on the first start, never printed."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from awgscale.core.files import PRIVATE_FILE_MODE

PASSWORD_BYTES = 18


def read_password(path: Path) -> str:
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w") as f:
            f.write(secrets.token_urlsafe(PASSWORD_BYTES) + "\n")
    return path.read_text().strip()
