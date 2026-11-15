"""Route tests of the pre-theme widget HUD's internals to the retained legacy class.

The themed HUD (``streamer_window.StreamerWindow``) draws one canvas scene, so it
has no pack geometry, Tk label widgets, edge grips or per-widget animators. Tests
listed in ``legacy_streamer_tests.txt`` exercise those mechanics and keep running
against ``app.LegacyStreamerWindow`` (kept for rollback). Equivalent behaviour of
the themed HUD is covered in ``test_streamer_themes.py``.
"""
from pathlib import Path

import pytest

_LIST = Path(__file__).with_name("legacy_streamer_tests.txt")
LEGACY = {line.strip() for line in _LIST.read_text(encoding="utf-8").splitlines()
          if line.strip() and not line.startswith("#")} if _LIST.exists() else set()


def _key(item):
    return f"{Path(item.fspath).name}::{item.originalname}"


def pytest_collection_modifyitems(items):
    for item in items:
        if _key(item) in LEGACY:
            item.add_marker(pytest.mark.legacy_streamer)
            item.fixturenames.insert(0, "_legacy_streamer_window")


@pytest.fixture
def _legacy_streamer_window(monkeypatch):
    from ped_hunter import app as appmod
    monkeypatch.setattr(appmod, "StreamerWindow", appmod.LegacyStreamerWindow)


def pytest_configure(config):
    config.addinivalue_line("markers", "legacy_streamer: runs against the retained pre-theme HUD")
