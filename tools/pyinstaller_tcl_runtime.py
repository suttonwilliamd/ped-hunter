"""Runtime Tcl/Tk path fix for PED Hunter PyInstaller bundles."""
from __future__ import annotations

import os
from pathlib import Path
import sys

bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
tcl_dir = bundle_root / "_tcl_data"
tk_dir = bundle_root / "_tk_data"

if tcl_dir.exists():
    os.environ["TCL_LIBRARY"] = str(tcl_dir)
if tk_dir.exists():
    os.environ["TK_LIBRARY"] = str(tk_dir)
