"""How the services write files. Standard library only."""
from __future__ import annotations

PRIVATE_FILE_MODE = 0o600       # read and written by the owner only: keys, passwords, the tunnel's peers
