"""Controlled Python fakes test the helper, NOT frozen Windows acceptance."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    "frozen_doctor_smoke", Path(__file__).resolve().parents[1] / "tools/frozen_doctor_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def payload(with_log=False):
    versions = {"python": "3.13.5", "watchdog": "6.0.0"}
    names = ["python", "tkinter", "watchdog"] + (["chat_log"] if with_log else [])
    return {"schema_version": 1, "status": "pass", "checks": [
        {"name": name, "status": "pass", "detail": smoke.DETAILS[name],
         "version": versions.get(name)} for name in names],
        "limitation": "Import/file checks do not prove display, game connection, or log-format health."}


def fake(tmp_path, action=""):
    script = tmp_path / "fake.py"
    script.write_text(
        "import json,os,pathlib,sys,time\n"
        "assert sys.argv[1:3] == ['doctor','--json']\n"
        "assert all(pathlib.Path(os.environ[k]).is_dir() for k in "
        "['USERPROFILE','HOME','APPDATA','LOCALAPPDATA','TEMP'])\n"
        "assert pathlib.Path.cwd() != pathlib.Path(os.environ['HOME'])\n"
        "with_log = '--chat-log' in sys.argv\n"
        "if with_log: assert pathlib.Path(sys.argv[4]).read_bytes() == " + repr(smoke.FIXTURE) + "\n"
        + action + "\n"
        + "print(json.dumps(" + repr(payload()) + " if not with_log else "
        + repr(payload(True)) + "))\n", encoding="utf-8")
    return [sys.executable, str(script)]


def test_success_in_isolated_environment(tmp_path):
    report = smoke.smoke(fake(tmp_path), timeout=3)
    assert report["status"] == "pass"
    assert len(report["runs"]) == 2
    assert "SYNTHETIC_PRIVATE" not in json.dumps(report)
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize("action,key", [
    ("sys.exit(1)", "exit_zero"),
    ("print('PRIVATE_EXCEPTION',file=sys.stderr)", "stderr_empty"),
    ("print('PRIVATE_PATH')", "json_schema"),
    ("pathlib.Path(os.environ['HOME'],'ped-hunter.sqlite3').touch()", "application_state_absent"),
    ("pathlib.Path(os.environ['APPDATA'],'catalog').mkdir(exist_ok=True)", "application_state_absent"),
    ("pathlib.Path('gui.lock').touch()", "application_state_absent"),
    ("pathlib.Path('_MEI_fake').mkdir(exist_ok=True)", "extraction_cleaned"),
    ("\nif with_log: pathlib.Path(sys.argv[4]).write_bytes(b'changed')", "fixture_unchanged"),
    ("print('X'*20000)", "output_bounded"),
])
def test_fail_closed(tmp_path, action, key):
    report = smoke.smoke(fake(tmp_path, action), timeout=3)
    assert report["status"] == "fail"
    assert not report["runs"][-1][key]
    text = json.dumps(report)
    assert "PRIVATE_EXCEPTION" not in text and "PRIVATE_PATH" not in text
    assert str(tmp_path) not in text


def test_timeout_and_launch_failure(tmp_path):
    report = smoke.smoke(fake(tmp_path, "time.sleep(2)"), timeout=0.1)
    assert report["status"] == "fail"
    assert all(run["timed_out"] for run in report["runs"])
    report = smoke.smoke([str(tmp_path / "absent")], timeout=0.1)
    assert report["status"] == "fail"
    assert all(not run["launch_ok"] for run in report["runs"])


@pytest.mark.parametrize("bad", [b"null", b"[]", b"{}", b"not-json", b'{"checks":[null]}'])
def test_invalid_output(bad):
    assert not smoke.validate(bad, False)["json_schema"]


def test_schema_redaction_and_explicit_failure():
    data = payload()
    data["checks"][2].update(status="fail", detail="dependency_unavailable", version=None)
    data["status"] = "fail"
    verdict = smoke.validate(json.dumps(data).encode(), False)
    assert verdict["json_schema"] and not verdict["checks_pass"]
    assert verdict["check_status"]["watchdog"] == "fail"
    assert verdict["redacted"]
    for field in ("detail", "version"):
        data = payload()
        data["checks"][2][field] = "C:\\private\\username"
        verdict = smoke.validate(json.dumps(data).encode(), False)
        assert not verdict["redacted"]
        assert "private" not in json.dumps(verdict)
    data = payload()
    data["schema_version"] = True
    assert not smoke.validate(json.dumps(data).encode(), False)["json_schema"]


def test_duplicate_keys_and_wrong_dependency_pin_fail():
    raw = json.dumps(payload()).replace('"status": "pass"',
                                        '"status": "PRIVATE_PATH", "status": "pass"', 1)
    assert not smoke.validate(raw.encode(), False)["json_schema"]
    data = payload()
    data["checks"][2]["version"] = "5.0.0"
    assert not smoke.validate(json.dumps(data).encode(), False)["expected_presence"]


def test_cli_missing_executable_writes_sanitized_failure(tmp_path):
    import subprocess
    report_path = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(Path(smoke.__file__)), str(tmp_path / "missing"),
                             "--report", str(report_path)], capture_output=True, timeout=10)
    assert result.returncode == 1
    assert json.loads(report_path.read_text())["status"] == "fail"
    assert str(tmp_path).encode() not in result.stdout + result.stderr
