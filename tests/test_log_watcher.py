from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import ped_hunter.log_watcher as log_watcher
from ped_hunter.log_watcher import LogWatcher, _LogFileEventHandler


class FakeObserver:
    instances: list["FakeObserver"] = []

    def __init__(self) -> None:
        self.scheduled = None
        self.started = False
        self.stopped = False
        self.join_timeout = None
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False) -> None:
        self.scheduled = (handler, path, recursive)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout=None) -> None:
        self.join_timeout = timeout



def test_log_file_event_handler_filters_exact_target_path(tmp_path: Path) -> None:
    target = tmp_path / "chat.log"
    seen: list[str] = []
    handler = _LogFileEventHandler(log_watcher._WatchedTarget.from_path(target), lambda: seen.append(str(target)))

    handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(target)))
    handler.on_created(SimpleNamespace(is_directory=False, src_path=str(target)))
    handler.on_moved(SimpleNamespace(is_directory=False, src_path=str(tmp_path / "old.log"), dest_path=str(target)))
    handler.on_deleted(SimpleNamespace(is_directory=False, src_path=str(target)))
    handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(tmp_path / "other.log")))

    assert seen == [str(target), str(target), str(target), str(target)]



def test_log_watcher_starts_and_stops_on_parent_directory(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "chat.log"
    target.write_text("test\n", encoding="utf-8")
    monkeypatch.setattr(log_watcher, "Observer", FakeObserver)
    FakeObserver.instances.clear()

    triggered: list[str] = []
    watcher = LogWatcher(target, lambda: triggered.append("changed"))

    assert watcher.available is True
    assert watcher.start() is True
    assert len(FakeObserver.instances) == 1
    fake = FakeObserver.instances[0]
    assert fake.started is True
    assert fake.scheduled is not None
    handler, path, recursive = fake.scheduled
    assert Path(path) == target.parent
    assert recursive is False
    assert handler is not None

    watcher.stop()
    assert fake.stopped is True
    assert fake.join_timeout == 1.5
