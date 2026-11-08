"""Native Windows DPI setup, before Tk creates any windows.

Without this, Windows bitmap-scales the whole UI, including already-rasterized
text. Keep fonts in device pixels and let Tk render them at the monitor's DPI.
"""
from __future__ import annotations

import sys


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [wintypes.HANDLE]
        set_context.restype = wintypes.BOOL
        if set_context(wintypes.HANDLE(-4)):  # PER_MONITOR_AWARE_V2
            return
        # Awareness may already be set by a manifest or an embedding host.
        # It cannot be changed after windows have been created.
        if ctypes.get_last_error() == 5:  # ERROR_ACCESS_DENIED
            return
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.WinDLL("shcore")
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) in (0, -2147024891):  # per-monitor / already set
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("user32").SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def window_ui_scale(window) -> float:
    """Use this window's monitor DPI, not Tk's process-wide startup scaling."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        try:
            get_dpi = ctypes.WinDLL("user32").GetDpiForWindow
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            dpi = get_dpi(window.winfo_id())
            if dpi:
                return dpi / 96
        except (AttributeError, OSError):
            pass
    return window.winfo_fpixels("1i") / 96
