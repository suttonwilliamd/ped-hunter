"""Byte-accurate, newline-buffered tailing for a log written by another process."""
from __future__ import annotations

import os
from pathlib import Path


class LogTail:
    """Read only completed lines, preserving split UTF-8 and detecting rotation.

    No file handle remains open between polls (important for Windows rotation).
    Offsets are byte offsets, never text-mode cookies. A short anchor detects
    truncate-and-regrow even when the new size has already passed our offset.
    """

    def __init__(self, path: Path, offset: int = 0) -> None:
        self.path = path
        self.offset = max(0, offset)
        self._identity: tuple[int, int] | None = None
        self._pending = b""
        self._anchor = b""
        self._discard_fragment = False
        try:
            with path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                self._identity = (stat.st_dev, stat.st_ino)
                if self.offset <= stat.st_size and self.offset:
                    stream.seek(max(0, self.offset - 32))
                    self._anchor = stream.read(min(32, self.offset))
                    # Start/Resume means new lines only, not the suffix of a
                    # historical unfinished line that happened to be at EOF.
                    self._discard_fragment = not self._anchor.endswith(b"\n")
        except OSError:
            pass  # Report open errors from read_lines; the worker can retry.

    def read_lines(self) -> list[str]:
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            reset = (self._identity is not None and identity != self._identity) or stat.st_size < self.offset
            if not reset and self._anchor:
                stream.seek(self.offset - len(self._anchor))
                reset = stream.read(len(self._anchor)) != self._anchor
            if reset:
                self.offset = 0
                self._pending = self._anchor = b""
                self._discard_fragment = False
            self._identity = identity
            stream.seek(self.offset)
            chunk = stream.read(1024 * 1024)  # Bound each poll during bursts.
            self.offset = stream.tell()
            if chunk:
                self._anchor = (self._anchor + chunk)[-32:]
        if not chunk:
            return []
        parts = (self._pending + chunk).split(b"\n")
        self._pending = parts.pop()
        if self._discard_fragment:
            if not parts:
                return []
            parts.pop(0)
            self._discard_fragment = False
        return [(line + b"\n").decode("utf-8-sig", errors="replace") for line in parts]
