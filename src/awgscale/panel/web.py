"""The files of the page: a build of web/ (its index and what it loads from /_next/) and its typefaces."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

BUILD_ID_PLACEHOLDER = b"__UI_ID__"
BUILD_ID_LENGTH = 12
ASSETS_PREFIX = "/_next/"
IMMUTABLE_PREFIX = "/_next/static/"        # a file under static/ is named by its content and never changes

# A path of the build the page may load:  /_next/static/chunks/0nwunsn8ls9nj.js
ASSET_RE = re.compile(
    r"""
    ^/_next/
    [A-Za-z0-9_~-]              # never starts with a dot
    [A-Za-z0-9_.~/-]{0,200}     # folders and the file name
    \Z
    """,
    re.VERBOSE,
)
# A font of the panel, the name after /fonts/:   inter-latin.woff2
FONT_RE = re.compile(
    r"""
    ^[a-z0-9-]{1,60}      # inter-latin
    \.woff2\Z            # .woff2
    """,
    re.VERBOSE,
)
ASSET_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
UNKNOWN_TYPE = "application/octet-stream"


class WebFiles:
    def __init__(self, web_dir: Path, fonts_dir: Path) -> None:
        self.__web_dir = Path(web_dir).resolve()
        self.__fonts_dir = fonts_dir
        self.__stamp: tuple[int, int] | None = None
        self.__build_id = ""

    def build_id(self) -> str:
        """A short id of the page as it is on disk (its index names every file of
        the build by a hash of its content, so it changes with any of them). The page
        is served with it and every state answer carries it, so a tab that was open
        across a deploy notices that it runs an older page and reloads itself."""
        index = self.__web_dir / "index.html"
        try:
            status = index.stat()
            stamp = (status.st_mtime_ns, status.st_size)
            if stamp != self.__stamp:
                self.__build_id = hashlib.sha256(index.read_bytes()).hexdigest()[:BUILD_ID_LENGTH]
                self.__stamp = stamp
        except OSError:
            pass
        return self.__build_id

    def index(self) -> bytes | None:
        """The page, stamped with the id of its build; None when there is no build."""
        try:
            page = (self.__web_dir / "index.html").read_bytes()
        except OSError:
            return None
        return page.replace(BUILD_ID_PLACEHOLDER, self.build_id().encode())

    def asset(self, path: str) -> tuple[bytes, str] | None:
        """A file of the build under /_next/ and its content type; None for anything outside the build."""
        if not ASSET_RE.match(path) or ".." in path:
            return None
        full = Path(str(self.__web_dir) + path).resolve()
        if not full.is_relative_to(self.__web_dir) or full == self.__web_dir:
            return None
        try:
            return full.read_bytes(), ASSET_TYPES.get(full.suffix, UNKNOWN_TYPE)
        except OSError:
            return None

    def font(self, name: str) -> bytes | None:
        if not FONT_RE.match(name):
            return None
        try:
            return (self.__fonts_dir / name).read_bytes()
        except OSError:
            return None
