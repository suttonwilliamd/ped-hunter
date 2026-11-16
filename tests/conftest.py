"""Shared test policy: legacy-HUD routing, native-UI selection and user-data isolation.

Legacy HUD: the themed HUD (``streamer_window.StreamerWindow``) draws one canvas
scene, so it has no pack geometry, Tk label widgets, edge grips or per-widget
animators. Tests listed in ``legacy_streamer_tests.txt`` exercise those mechanics
against ``app.LegacyStreamerWindow`` (kept for rollback, not selected at runtime),
so they are opt-in: pass ``--legacy-hud``. Equivalent themed behaviour is covered
in ``test_streamer_themes.py``.

Native UI: tests listed in ``native_ui_tests.txt`` (whole files or single tests)
create real Tk windows, take focus or drive input. ``--no-gui`` deselects them and
refuses any Tk root or sound process-wide, so an unlisted window-opening test fails
instead of reaching the desktop. ``-m native_ui`` selects only them. See
docs/testing.md for the routine (fast, non-intrusive) and deferred desktop paths.
"""
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def _entries(name):
    path = _HERE / name
    if not path.exists():
        return set()
    return {line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()} - {""}


LEGACY = _entries("legacy_streamer_tests.txt")
NATIVE = _entries("native_ui_tests.txt")


def _key(item):
    return f"{Path(item.fspath).name}::{item.originalname}"


def _is_native(item):
    return Path(item.fspath).name in NATIVE or _key(item) in NATIVE


class NativeUIRefused(RuntimeError):
    """Raised instead of opening a window or playing a sound under --no-gui."""


def pytest_addoption(parser):
    parser.addoption("--legacy-hud", action="store_true",
                     help="also run tests of the retained pre-theme LegacyStreamerWindow")
    parser.addoption("--no-gui", action="store_true",
                     help="skip native-UI tests and refuse Tk windows/sounds (shared-desktop safe)")


def pytest_configure(config):
    config.addinivalue_line("markers", "legacy_streamer: runs against the retained pre-theme HUD")
    config.addinivalue_line("markers", "native_ui: opens real Tk windows; defer while the desktop is in use")
    if not config.getoption("--no-gui"):
        return
    # Process-wide, before any fixture runs: a Tk root (PedHunterApp, messagebox,
    # implicit default root) or a sound fails the test instead of reaching the desktop.
    import tkinter

    def refuse(*_args, **_kwargs):
        raise NativeUIRefused("native UI refused under --no-gui; list this test in tests/native_ui_tests.txt")

    tkinter.Tk.__init__ = refuse
    try:
        import winsound
    except ImportError:
        return
    winsound.PlaySound = refuse


@pytest.hookimpl(tryfirst=True)  # mark before -m deselection runs
def pytest_collection_modifyitems(config, items):
    run_legacy = config.getoption("--legacy-hud")
    no_gui = config.getoption("--no-gui")
    kept, dropped = [], []
    for item in items:
        native = _is_native(item) or _key(item) in LEGACY
        if (_key(item) in LEGACY and not run_legacy) or (native and no_gui):
            dropped.append(item)
            continue
        if native:
            item.add_marker(pytest.mark.native_ui)
        if _key(item) in LEGACY:
            item.add_marker(pytest.mark.legacy_streamer)
            item.fixturenames.insert(0, "_legacy_streamer_window")
        kept.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


@pytest.fixture
def _legacy_streamer_window(monkeypatch):
    from ped_hunter import app as appmod
    monkeypatch.setattr(appmod, "StreamerWindow", appmod.LegacyStreamerWindow)


@pytest.fixture(autouse=True)
def _never_touch_real_user_data(tmp_path_factory, monkeypatch):
    """Tests must not open %LOCALAPPDATA%/ped-hunter (real hunts) or its GUI lock."""
    from ped_hunter import cli
    from ped_hunter.storage import Store
    private = tmp_path_factory.mktemp("default-user-data")
    monkeypatch.setattr(Store, "default_db_path", staticmethod(lambda: private / "ped-hunter.sqlite3"))
    monkeypatch.setattr(cli, "_gui_lock_path", lambda: private / "PED-Hunter.gui.lock")
