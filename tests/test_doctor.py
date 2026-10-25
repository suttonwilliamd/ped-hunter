"""Diagnostics use only disposable fixtures; no native GUI acceptance implied."""
import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import urllib.request

import pytest

from ped_hunter import cli, doctor


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(doctor.importlib, "import_module", lambda name: object())
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "6.0.0")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())


def invoke(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["doctor", *args])
    return code, out.getvalue()


def test_success_schema(healthy):
    code, text = invoke("--json")
    result = json.loads(text)
    assert code == 0
    assert set(result) == {"schema_version", "status", "checks", "limitation"}
    assert result["schema_version"] == 1 and result["status"] == "pass"
    assert [c["name"] for c in result["checks"]] == ["python", "tkinter", "watchdog"]
    for check in result["checks"]:
        assert set(check) == {"name", "status", "detail", "version"}
        assert check["status"] == "pass"
    assert "do not prove" in result["limitation"]


@pytest.mark.parametrize("failure", ["missing", "old", "prerelease", "module_missing", "unsafe_version"])
def test_dependency_failures(healthy, monkeypatch, failure):
    def version(name):
        if failure == "missing":
            raise doctor.importlib.metadata.PackageNotFoundError("/Users/private/secret")
        return {"old": "5.0.0", "prerelease": "6.0.0rc1",
                "unsafe_version": "/Users/private/secret"}.get(failure, "6.0.0")
    monkeypatch.setattr(doctor.importlib.metadata, "version", version)
    if failure == "module_missing":
        monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    code, text = invoke("--json")
    assert code == 1 and json.loads(text)["status"] == "fail"
    assert "private" not in text and "secret" not in text


def test_import_exception_redaction(healthy, monkeypatch):
    def unavailable(name):
        print("/Users/private/secret")
        raise ImportError("C:\\Users\\private\\secret")
    monkeypatch.setattr(doctor.importlib, "import_module", unavailable)
    for args in [(), ("--json",)]:
        code, text = invoke(*args)
        assert code == 1 and "import_unavailable" in text
        assert "private" not in text and "secret" not in text


def test_python_minimum(healthy, monkeypatch):
    monkeypatch.setattr(doctor.sys, "version_info", (3, 10, 9))
    code, text = invoke("--json")
    assert code == 1 and "requires_python_3_11" in text


@pytest.mark.parametrize("kind,detail", [("file", "readable"), ("directory", "not_regular_file"),
                                          ("missing", "missing"), ("unreadable", "unreadable")])
def test_explicit_path(healthy, tmp_path, monkeypatch, kind, detail):
    path = tmp_path / "private-user-chat.log"
    if kind in {"file", "unreadable"}:
        path.write_text("SECRET CHAT CONTENTS", encoding="utf-8")
    elif kind == "directory":
        path.mkdir()
    if kind == "unreadable":
        # Deterministic on root/Linux and Windows ACL environments alike.
        def denied(*args, **kwargs):
            raise PermissionError(f"access denied {path}")
        monkeypatch.setattr(doctor.os, "open", denied)
    code, text = invoke("--json", "--chat-log", str(path))
    result = json.loads(text)
    assert result["checks"][-1]["detail"] == detail
    assert code == (0 if kind == "file" else 1)
    assert str(tmp_path) not in text and path.name not in text and "SECRET" not in text


def test_no_side_effects(healthy, monkeypatch, tmp_path):
    path = tmp_path / "chat.log"
    path.write_bytes(b"NEVER READ THIS")
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden side effect")
    for target, name in [(sqlite3, "connect"), (urllib.request, "urlopen"),
                         (socket, "create_connection"), (socket.socket, "connect"),
                         (cli, "_launch_gui"), (cli, "_single_gui_instance"),
                         (cli, "_monitor"), (cli, "_seed_data"),
                         (cli.Catalog, "load"), (cli.Store, "__init__"),
                         (cli.LogTail, "__init__"), (Path, "home"),
                         (builtins, "open"), (Path, "open"), (os, "read")]:
        monkeypatch.setattr(target, name, forbidden)
    code, text = invoke("--chat-log", str(path))
    assert code == 0 and "readable" in text
    # Use os-level metadata only while read guards remain installed.
    assert os.stat(path).st_size == len(before)


def test_real_tk_import_never_constructs_window(healthy, monkeypatch):
    # Exercise the real import, not the success stub, with constructor tripwires.
    monkeypatch.undo()
    try:
        import tkinter
    except ImportError:
        pytest.skip("Tkinter not installed; unavailable-import behavior covered separately")
    def forbidden(*args, **kwargs):
        raise AssertionError("must not create Tk/Tcl")
    monkeypatch.setattr(tkinter, "Tk", forbidden)
    monkeypatch.setattr(tkinter, "Tcl", forbidden)
    assert doctor.diagnose()["checks"][1]["status"] == "pass"


@pytest.mark.parametrize("args", [["--unknown", "/Users/private/secret"], ["--chat-log"],
                                  ["--json", "/Users/private/secret"]])
def test_usage_redacted(args, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["doctor", *args])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "private" not in captured.err and "secret" not in captured.err


def test_subprocess_isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    script = '''
import sys
class Guard:
    def find_spec(self, fullname, *args):
        if fullname in {"ped_hunter.app", "ped_hunter.preview"}:
            raise AssertionError("GUI import forbidden")
sys.meta_path.insert(0, Guard())
from ped_hunter.cli import main
raise SystemExit(main(["doctor", "--json"]))
'''
    result = subprocess.run([sys.executable, "-c", script],
                            env=env, capture_output=True, text=True, timeout=20)
    report = json.loads(result.stdout)
    assert result.returncode == (0 if report["status"] == "pass" else 1)
    assert result.stderr == "" and str(home) not in result.stdout
    assert list(home.iterdir()) == []


def test_existing_dispatch(monkeypatch):
    monkeypatch.setattr(cli, "_launch_gui", lambda: 42)
    assert cli.main([]) == 42
    assert cli.main(["gui"]) == 42
    monkeypatch.setattr(cli, "_stats", lambda db, limit: 43)
    assert cli.main(["stats"]) == 43
    monkeypatch.setattr(cli, "_weapon_lookup", lambda query: 44)
    assert cli.main(["weapon", "fixture"]) == 44
    monkeypatch.setattr(cli, "_seed_data", lambda force: 45)
    assert cli.main(["seed-data"]) == 45
    monkeypatch.setattr(cli, "_monitor", lambda *args: 46)
    assert cli.main(["monitor", "--chat-log", "fixture"]) == 46
