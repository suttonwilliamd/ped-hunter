"""File-system watcher integration for PED Hunter log ingestion."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import os

try:  # pragma: no cover - exercised indirectly when watchdog is installed
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:  # pragma: no cover - fallback path for environments without watchdog
    FileSystemEvent = object  # type: ignore[assignment]
    FileSystemEventHandler = object  # type: ignore[assignment]
    Observer = None  # type: ignore[assignment]


ChangeCallback = Callable[[], None]


def _normalize_path(path: Path | str) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


@dataclass(slots=True)
class _WatchedTarget:
    path: Path
    normalized: str

    @classmethod
    def from_path(cls, path: Path | str) -> "_WatchedTarget":
        resolved = Path(path).expanduser().resolve(strict=False)
        return cls(path=resolved, normalized=_normalize_path(resolved))

    def matches(self, candidate: Path | str | None) -> bool:
        if not candidate:
            return False
        return _normalize_path(candidate) == self.normalized


class _LogFileEventHandler(FileSystemEventHandler):
    def __init__(self, target: _WatchedTarget, on_change: ChangeCallback) -> None:
        self._target = target
        self._on_change = on_change

    def _notify_if_target(self, candidate: Path | str | None) -> None:
        if self._target.matches(candidate):
            self._on_change()

    def on_modified(self, event: FileSystemEvent) -> None:  # pragma: no cover - exercised through the watcher
        if getattr(event, "is_directory", False):
            return
        self._notify_if_target(getattr(event, "src_path", None))

    def on_created(self, event: FileSystemEvent) -> None:  # pragma: no cover - exercised through the watcher
        if getattr(event, "is_directory", False):
            return
        self._notify_if_target(getattr(event, "src_path", None))

    def on_deleted(self, event: FileSystemEvent) -> None:  # pragma: no cover - exercised through the watcher
        if getattr(event, "is_directory", False):
            return
        self._notify_if_target(getattr(event, "src_path", None))

    def on_moved(self, event: FileSystemEvent) -> None:  # pragma: no cover - exercised through the watcher
        if getattr(event, "is_directory", False):
            return
        self._notify_if_target(getattr(event, "src_path", None))
        self._notify_if_target(getattr(event, "dest_path", None))


class LogWatcher:
    """Watch the parent directory of ``chat.log`` and wake an ingestion worker."""

    def __init__(self, log_path: Path | str, on_change: ChangeCallback) -> None:
        self._target = _WatchedTarget.from_path(log_path)
        self._on_change = on_change
        self._observer = None
        self._handler = _LogFileEventHandler(self._target, self._on_change)

    @property
    def available(self) -> bool:
        return Observer is not None

    @property
    def target_path(self) -> Path:
        return self._target.path

    def start(self) -> bool:
        if Observer is None:
            return False
        if self._observer is not None:
            return True
        parent = self._target.path.parent
        if not parent.exists():
            return False
        observer = Observer()
        observer.schedule(self._handler, str(parent), recursive=False)
        observer.start()
        self._observer = observer
        return True

    def stop(self) -> None:
        observer = self._observer
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=1.5)
        self._observer = None
