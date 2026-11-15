"""Route tests of the pre-theme widget HUD's internals to the retained legacy class.

The themed HUD (``streamer_window.StreamerWindow``) draws one canvas scene, so it
has no pack geometry, Tk label widgets, edge grips or per-widget animators. Tests
listed in ``legacy_streamer_tests.txt`` exercise those mechanics and keep running
against ``app.LegacyStreamerWindow`` (kept for rollback). Equivalent behaviour of
the themed HUD is covered in ``test_streamer_themes.py``.

That class is not selected at runtime, so these tests are opt-in: pass
``--legacy-hud`` (e.g. before pointing ``app.StreamerWindow`` back at it).
"""
from pathlib import Path

import pytest

_LIST = Path(__file__).with_name("legacy_streamer_tests.txt")
LEGACY = {line.strip() for line in _LIST.read_text(encoding="utf-8").splitlines()
          if line.strip() and not line.startswith("#")} if _LIST.exists() else set()


def _key(item):
    return f"{Path(item.fspath).name}::{item.originalname}"


def pytest_addoption(parser):
    parser.addoption("--legacy-hud", action="store_true",
                     help="also run tests of the retained pre-theme LegacyStreamerWindow")


def pytest_collection_modifyitems(config, items):
    run_legacy = config.getoption("--legacy-hud")
    kept, skipped = [], []
    for item in items:
        if _key(item) in LEGACY:
            if not run_legacy:
                skipped.append(item)
                continue
            item.add_marker(pytest.mark.legacy_streamer)
            item.fixturenames.insert(0, "_legacy_streamer_window")
        kept.append(item)
    if skipped:
        config.hook.pytest_deselected(items=skipped)
        items[:] = kept


@pytest.fixture
def _legacy_streamer_window(monkeypatch):
    from ped_hunter import app as appmod
    monkeypatch.setattr(appmod, "StreamerWindow", appmod.LegacyStreamerWindow)


def pytest_configure(config):
    config.addinivalue_line("markers", "legacy_streamer: runs against the retained pre-theme HUD")


@pytest.fixture(autouse=True)
def _never_touch_real_user_data(tmp_path_factory, monkeypatch):
    """Tests must not open %LOCALAPPDATA%/ped-hunter (real hunts) or its GUI lock."""
    from ped_hunter import cli
    from ped_hunter.storage import Store
    private = tmp_path_factory.mktemp("default-user-data")
    monkeypatch.setattr(Store, "default_db_path", staticmethod(lambda: private / "ped-hunter.sqlite3"))
    monkeypatch.setattr(cli, "_gui_lock_path", lambda: private / "PED-Hunter.gui.lock")
