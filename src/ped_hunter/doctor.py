"""Read-only setup checks. Never inspect game data or initialize application state."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import stat
import sys


LIMITATION = "Import/file checks do not prove display, game connection, or log-format health."


def _version(value: str) -> str | None:
    # Metadata is untrusted: never echo arbitrary strings (including paths).
    return value if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:(?:a|b|rc)[0-9]+)?", value) else None


def diagnose(chat_log: str | None = None) -> dict:
    checks = []

    def add(name, passed, detail, version=None):
        checks.append({"name": name, "status": "pass" if passed else "fail",
                       "detail": detail, "version": version})

    supported = sys.version_info >= (3, 11)
    add("python", supported, "supported" if supported else "requires_python_3_11",
        ".".join(map(str, sys.version_info[:3])))
    try:
        # Import only: no Tk(), Tcl(), display probing, or application imports.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            importlib.import_module("tkinter")
    except Exception:
        add("tkinter", False, "import_unavailable")
    else:
        add("tkinter", True, "import_available")

    # Keep aligned with pyproject.toml's sole declared runtime dependency.
    try:
        version = _version(importlib.metadata.version("watchdog"))
        present = importlib.util.find_spec("watchdog") is not None
        parts = tuple(map(int, re.match(r"[0-9.]+", version)[0].split("."))) if version else ()
        release = (parts + (0, 0, 0))[:3]
        prerelease = bool(version and re.search(r"a|b|rc", version))
        supported = present and bool(version) and (
            release > (6, 0, 0) or (release == (6, 0, 0) and not prerelease)
        )
        add("watchdog", supported, "available" if supported else "requires_watchdog_6", version)
    except Exception:
        add("watchdog", False, "dependency_unavailable")

    if chat_log is not None:
        try:
            if not stat.S_ISREG(os.stat(chat_log).st_mode):
                add("chat_log", False, "not_regular_file")
            else:
                # No reads. NONBLOCK also prevents a replaced file becoming a FIFO hang.
                fd = os.open(chat_log, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
                try:
                    regular = stat.S_ISREG(os.fstat(fd).st_mode)
                    add("chat_log", regular, "readable" if regular else "not_regular_file")
                finally:
                    os.close(fd)
        except FileNotFoundError:
            add("chat_log", False, "missing")
        except (OSError, ValueError):
            add("chat_log", False, "unreadable")

    return {"schema_version": 1,
            "status": "pass" if all(c["status"] == "pass" for c in checks) else "fail",
            "checks": checks, "limitation": LIMITATION}


def run(chat_log: str | None = None, as_json: bool = False) -> int:
    result = diagnose(chat_log)
    if as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        for check in result["checks"]:
            version = f" ({check['version']})" if check["version"] else ""
            print(f"{check['name']}: {check['status']} - {check['detail']}{version}")
        print(LIMITATION)
    return 0 if result["status"] == "pass" else 1
