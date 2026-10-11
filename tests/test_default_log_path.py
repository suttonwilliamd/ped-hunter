from pathlib import Path
import ctypes
import sys
from types import SimpleNamespace

from ped_hunter.app import _default_chat_log_path


def test_windows_redirected_documents_is_used(tmp_path, monkeypatch):
    documents = tmp_path / "OneDrive" / "Documents"
    seen = []
    def get_folder(hwnd, csidl, token, flags, buffer):
        seen.append(csidl)
        buffer.value = str(documents)
        return 0
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(SHGetFolderPathW=get_folder), raising=False)
    assert _default_chat_log_path() == documents / "Entropia Universe" / "chat.log"
    assert seen == [5]


def test_windows_folder_api_failure_has_safe_fallback(tmp_path, monkeypatch):
    def get_folder(*args):
        return -1
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(SHGetFolderPathW=get_folder), raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _default_chat_log_path() == tmp_path / "Documents" / "Entropia Universe" / "chat.log"


def test_non_windows_home_documents_default(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert _default_chat_log_path() == tmp_path / "Documents" / "Entropia Universe" / "chat.log"
