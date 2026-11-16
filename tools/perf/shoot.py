"""Capture the real PED Hunter main window (run via hidden_run.py; nothing reaches the screen).

usage: hidden_run.py <timeout> <python> shoot.py <src-dir> <out-dir> <scale> <scenario.py>
The scenario file is exec'd with: app, shot(name), size(w, h), tab(name), pump(ms), log(...)
Runs on a private copy of the hunt database; never opens %LOCALAPPDATA%/ped-hunter.
"""
import ctypes, os, shutil, sys, time, traceback
from ctypes import wintypes
from pathlib import Path

SRC, OUT, SCALE, SCENARIO = sys.argv[1], Path(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
OUT.mkdir(parents=True, exist_ok=True)
sys.stdout = sys.stderr = (OUT / "log.txt").open("w", encoding="utf-8", buffering=1)
sys.path.insert(0, SRC)
HERE = Path(os.environ["PED_PERF_DIR"])  # holds data/real.sqlite3 (a copy; never the live database)
try:
    work = HERE / "data" / f"work-{OUT.name}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    db = work / "ped-hunter.sqlite3"
    shutil.copyfile(HERE / "data" / "real.sqlite3", db)

    from ped_hunter import cli, app as appmod, dpi
    from ped_hunter.storage import Store
    Store.default_db_path = staticmethod(lambda: db)
    cli._gui_lock_path = lambda: work / "PED-Hunter.gui.lock"
    appmod._default_chat_log_path = lambda: work / "chat.log"
    (work / "chat.log").write_text("", encoding="utf-8")
    if SCALE != 1.0:
        appmod.window_ui_scale = lambda _w: SCALE
    import tkinter.messagebox as mb
    for name in ("showinfo", "showerror", "showwarning"):
        setattr(mb, name, lambda *a, **k: print("MESSAGEBOX", name, a[:2]))
    mb.askyesno = lambda *a, **k: (print("ASKYESNO", a[:2]), True)[1]
    mb.askyesnocancel = lambda *a, **k: (print("ASKYNC", a[:2]), False)[1]

    from PIL import Image
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    user32.GetParent.restype = wintypes.HWND; user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC; user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC; gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]; gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("a", ctypes.c_long), ("b", ctypes.c_long),
                    ("c", wintypes.DWORD), ("d", wintypes.DWORD)]

    def grab(window, flags=2):
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        wdc = user32.GetWindowDC(hwnd); mdc = gdi32.CreateCompatibleDC(wdc)
        bmp = gdi32.CreateCompatibleBitmap(wdc, w, h); gdi32.SelectObject(mdc, bmp)
        ok = user32.PrintWindow(hwnd, mdc, flags)
        info = BMIH(); info.biSize = ctypes.sizeof(BMIH); info.biWidth = w; info.biHeight = -h
        info.biPlanes = 1; info.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0)
        gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(hwnd, wdc)
        return ok, Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")

    app = appmod.PedHunterApp(store=Store(db))
    app.geometry("+40+40")

    def log(*a):
        print(*a, flush=True)

    def pump(ms=300):
        end = time.perf_counter() + ms / 1000
        while time.perf_counter() < end:
            app.update()
            time.sleep(0.01)

    def size(w, h, window=None):
        (window or app).geometry(f"{round(w)}x{round(h)}")
        pump(350)
        target = window or app
        log(f"size requested {w}x{h} -> actual {target.winfo_width()}x{target.winfo_height()}")

    def tab(name):
        for tab_id in app.notebook.tabs():
            if app.notebook.tab(tab_id, "text").strip().casefold() == name.casefold():
                app.notebook.select(tab_id); pump(350); return
        raise KeyError(name + " not in " + str([app.notebook.tab(t, "text") for t in app.notebook.tabs()]))

    def shot(name, window=None):
        pump(150)
        ok, image = grab(window or app)
        extrema = image.convert("L").getextrema()
        image.save(OUT / f"{name}.png")
        log(f"shot {name}: ok={ok} size={image.size} luma={extrema}")

    pump(600)
    exec(compile(Path(SCENARIO).read_text(encoding="utf-8"), SCENARIO, "exec"))
    app.destroy()
    print("DONE")
except BaseException:
    traceback.print_exc()
    print("FAILED")
    os._exit(1)
os._exit(0)
