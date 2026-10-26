"""Bounded, stdlib-only frozen doctor probe. Never publish child output or paths."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

LIMIT = 16 * 1024
FIXTURE = b"SYNTHETIC_PRIVATE_CHAT_SENTINEL\r\nNot real game data.\r\n"
DETAILS = {"python": "supported", "tkinter": "import_available",
           "watchdog": "available", "chat_log": "readable"}
SAFE_DETAILS = {"python": ("supported", "requires_python_3_11"),
                "tkinter": ("import_available", "import_unavailable"),
                "watchdog": ("available", "requires_watchdog_6", "dependency_unavailable"),
                "chat_log": ("readable", "not_regular_file", "missing", "unreadable")}


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def validate(raw: bytes, with_log: bool) -> dict:
    """Return only fixed booleans/names, never untrusted diagnostic text."""
    result = {"json_schema": False, "checks_pass": False,
              "expected_presence": False, "redacted": False}
    try:
        data = json.loads(raw, object_pairs_hook=unique_object)
        names = ["python", "tkinter", "watchdog"] + (["chat_log"] if with_log else [])
        checks = data["checks"]
        result["json_schema"] = (
            type(data["schema_version"]) is int and data["schema_version"] == 1
            and set(data) == {"schema_version", "status", "checks", "limitation"}
            and isinstance(checks, list) and len(checks) == len(names)
            and all(set(c) == {"name", "status", "detail", "version"} for c in checks)
            and [c["name"] for c in checks] == names)
        if not result["json_schema"]:
            return result
        result["check_status"] = {c["name"]: c["status"] if c["status"] in ("pass", "fail")
                                  else "invalid" for c in checks}
        result["checks_pass"] = data["status"] == "pass" and all(c["status"] == "pass" for c in checks)
        versions = {c["name"]: c["version"] for c in checks}
        version_ok = lambda v: isinstance(v, str) and len(v) <= 32 and re.fullmatch(
            r"[0-9]+(?:\.[0-9]+){1,3}(?:(?:a|b|rc)[0-9]+)?", v) is not None
        result["expected_presence"] = (
            version_ok(versions["python"]) and versions["python"].startswith("3.13.")
            and versions["watchdog"] == "6.0.0"  # CI's explicit dependency pin
            and all(c["detail"] == DETAILS[c["name"]] for c in checks))
        # Strict value allowlist covers every string: no paths, exceptions, fixture
        # bytes, username or arbitrary metadata can sneak into a shared report.
        result["redacted"] = (
            data["status"] in {"pass", "fail"}
            and data["limitation"] == "Import/file checks do not prove display, game connection, or log-format health."
            and all(c["status"] in {"pass", "fail"} and c["detail"] in SAFE_DETAILS[c["name"]]
                    and (c["version"] is None or version_ok(c["version"])) for c in checks))
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return result


def stop_child(child: subprocess.Popen) -> None:
    # One-file Windows bootloaders have a child process; stop that tree too.
    if os.name == "nt":
        taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/PID", str(child.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if child.poll() is None:
        child.kill()


def run_child(command: list[str], cwd: Path, env: dict, timeout: float) -> tuple[dict, bytes]:
    evidence = {"exit_zero": False, "timed_out": False, "output_bounded": True,
                "stderr_empty": False, "launch_ok": False}
    # Files avoid unbounded in-memory capture. Poll bounds temporary disk output;
    # these raw files are deleted and are NEVER copied to the evidence report.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            child = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=err,
                                     stdin=subprocess.DEVNULL)
        except OSError:
            return evidence, b""
        evidence["launch_ok"] = True
        deadline = time.monotonic() + timeout
        while child.poll() is None:
            if time.monotonic() >= deadline:
                evidence["timed_out"] = True
                stop_child(child)
                break
            if os.fstat(out.fileno()).st_size > LIMIT or os.fstat(err.fileno()).st_size > LIMIT:
                evidence["output_bounded"] = False
                stop_child(child)
                break
            time.sleep(0.02)
        child.wait(timeout=5)
        evidence["output_bounded"] &= (os.fstat(out.fileno()).st_size <= LIMIT
                                          and os.fstat(err.fileno()).st_size <= LIMIT)
        evidence["exit_zero"] = child.returncode == 0
        evidence["stderr_empty"] = os.fstat(err.fileno()).st_size == 0
        out.seek(0)
        return evidence, out.read(LIMIT) if evidence["output_bounded"] else b""


def smoke(command: list[str], timeout: float = 60) -> dict:
    report = {"report_schema": 1, "status": "fail", "runs": []}
    with tempfile.TemporaryDirectory(prefix="frozen-doctor-") as temporary:
        root = Path(temporary)
        for name in ("profile", "home", "appdata", "localappdata", "cwd", "temp"):
            (root / name).mkdir()
        fixture = root / "cwd" / "synthetic-private-chat.log"
        fixture.write_bytes(FIXTURE)
        expected = {p.relative_to(root) for p in root.rglob("*")}
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC") if key in os.environ}
        env.update({key: str(root / directory) for key, directory in {
            "USERPROFILE": "profile", "HOME": "home", "APPDATA": "appdata",
            "LOCALAPPDATA": "localappdata", "TEMP": "temp", "TMP": "temp",
            "TMPDIR": "temp"}.items()})
        for with_log in (False, True):
            args = ["doctor", "--json"] + (["--chat-log", str(fixture)] if with_log else [])
            evidence, raw = run_child(command + args, root / "cwd", env, timeout)
            evidence.update(validate(raw, with_log))
            evidence["mode"] = "explicit_synthetic_log" if with_log else "no_log"
            try:
                evidence["fixture_unchanged"] = fixture.read_bytes() == FIXTURE
            except OSError:
                evidence["fixture_unchanged"] = False
            additions = {p.relative_to(root) for p in root.rglob("*")} - expected
            # The existing spec extracts one-file contents into cwd/_MEI*. That
            # bootloader state is distinct from app DB/catalog/home/lock writes.
            extraction = {p for p in additions if p.parts[0] == "cwd"
                          and len(p.parts) > 1 and p.parts[1].startswith("_MEI")}
            evidence["application_state_absent"] = not (additions - extraction)
            evidence["extraction_cleaned"] = not extraction
            report["runs"].append(evidence)
    required = ("exit_zero", "output_bounded", "stderr_empty", "launch_ok", "json_schema",
                "checks_pass", "expected_presence", "redacted", "fixture_unchanged",
                "application_state_absent", "extraction_cleaned")
    if all(all(run[key] for key in required) and not run["timed_out"] for run in report["runs"]):
        report["status"] = "pass"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Write a fail-closed report even for unexpected helper errors; no exception
    # messages, environment, command line or child stdout are ever shared.
    report = {"report_schema": 1, "status": "fail", "helper_error": True}
    try:
        report = smoke([str(args.executable.resolve())])
    except Exception:
        pass
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Frozen doctor smoke: " + report["status"] + " (sanitized report written)")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
