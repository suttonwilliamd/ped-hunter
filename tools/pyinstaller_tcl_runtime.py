"""Runtime Tcl/Tk path fix for PED Hunter PyInstaller bundles."""
from __future__ import annotations

import os
from pathlib import Path
import sys

bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
tcl_dir = bundle_root / "_tcl_data"
tk_dir = bundle_root / "_tk_data"

if tcl_dir.exists():
    # Tcl accepts Windows paths, but in one-file bundles the backslash form can
    # still be reported as an unusable braced path by Tcl's init search. Forward
    # slashes are the most reliable representation on Windows.
    os.environ["TCL_LIBRARY"] = tcl_dir.as_posix()
if tk_dir.exists():
    os.environ["TK_LIBRARY"] = tk_dir.as_posix()
