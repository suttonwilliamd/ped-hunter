from types import SimpleNamespace

import pytest

from ped_hunter import dpi


class NativeFunction:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


def test_enables_per_monitor_v2(monkeypatch):
    import ctypes
    function = NativeFunction(1)
    monkeypatch.setattr(dpi.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(
        SetProcessDpiAwarenessContext=function), raising=False)
    dpi.enable_high_dpi()
    assert function.calls[0][0].value == ctypes.c_void_p(-4).value


def test_older_windows_falls_back_to_per_monitor_v1(monkeypatch):
    import ctypes
    function = NativeFunction(0)
    monkeypatch.setattr(dpi.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **k: SimpleNamespace(
        SetProcessDpiAwareness=function) if name == 'shcore' else SimpleNamespace(), raising=False)
    dpi.enable_high_dpi()
    assert function.calls == [(2,)]


def test_existing_awareness_is_preserved(monkeypatch):
    import ctypes
    calls = []
    def load(name, **kwargs):
        calls.append(name)
        return SimpleNamespace(SetProcessDpiAwarenessContext=NativeFunction(0))
    monkeypatch.setattr(dpi.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'WinDLL', load, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    dpi.enable_high_dpi()
    assert calls == ['user32']


@pytest.mark.parametrize('value', [96, 120, 144, 192])
def test_window_scale_uses_native_monitor_dpi(monkeypatch, value):
    import ctypes
    monkeypatch.setattr(dpi.sys, 'platform', 'win32')
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: SimpleNamespace(
        GetDpiForWindow=NativeFunction(value)), raising=False)
    window = SimpleNamespace(winfo_id=lambda: 123, winfo_fpixels=lambda _: 96)
    assert dpi.window_ui_scale(window) == value / 96


def test_non_windows_uses_tk_and_does_not_load_native_libraries(monkeypatch):
    monkeypatch.setattr(dpi.sys, 'platform', 'linux')
    dpi.enable_high_dpi()
    assert dpi.window_ui_scale(SimpleNamespace(winfo_fpixels=lambda _: 144)) == 1.5
