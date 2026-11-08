"""Execute the spec without a build; inspect its actual packager arguments.

The metadata producer is replaced with independent sentinel data, not a parser
for implementation spelling. Native Windows freezing is still a separate gate.
"""
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace

import pytest


SPEC = Path(__file__).resolve().parents[1] / "PED-Hunter.spec"


@pytest.mark.parametrize("tcl_names", [(), ("tcl8.6",), ("tcl8.6", "tk8.6")])
def test_spec_collects_watchdog_and_metadata_and_preserves_packaging(monkeypatch, tmp_path, tcl_names):
    python_root = tmp_path / "python"
    for name in tcl_names:
        (python_root / "tcl" / name).mkdir(parents=True)
    monkeypatch.setattr(sys, "base_prefix", str(python_root))

    # Deliberately unrelated to the real installed version/path; two entries
    # catch partial collection as well as a missing or nested datas list.
    expected_metadata = [
        (str(tmp_path / "producer" / "watchdog-sentinel.dist-info"), "watchdog-sentinel.dist-info"),
        (str(tmp_path / "producer" / "extra-metadata"), "metadata-sentinel"),
    ]
    requests = []

    def metadata_producer(*args, **kwargs):
        requests.append((args, kwargs))
        return list(expected_metadata)

    # PyInstaller is a build-only dependency, not required by the source suite.
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.copy_metadata = metadata_producer
    package = ModuleType("PyInstaller")
    package.__path__ = []
    utils = ModuleType("PyInstaller.utils")
    utils.__path__ = []
    package.utils = utils
    utils.hooks = hooks
    for name, module in (("PyInstaller", package), ("PyInstaller.utils", utils),
                         ("PyInstaller.utils.hooks", hooks)):
        monkeypatch.setitem(sys.modules, name, module)

    calls = {}
    analysis = SimpleNamespace(pure=object(), scripts=object(), binaries=object(), datas=object())
    pyz = object()

    def capture_analysis(*args, **kwargs):
        calls["analysis"] = (args, kwargs)
        return analysis

    def capture_pyz(*args, **kwargs):
        calls["pyz"] = (args, kwargs)
        return pyz

    def capture_exe(*args, **kwargs):
        calls["exe"] = (args, kwargs)
        return object()

    runpy.run_path(str(SPEC), init_globals={
        "Analysis": capture_analysis, "PYZ": capture_pyz, "EXE": capture_exe,
    })

    args, options = calls["analysis"]
    expected_tcl = []
    if "tcl8.6" in tcl_names:
        expected_tcl.append((str(python_root / "tcl" / "tcl8.6"), "_tcl_data"))
    if "tk8.6" in tcl_names:
        expected_tcl.append((str(python_root / "tcl" / "tk8.6"), "_tk_data"))
    # Check actual consumer data first: baseline fails here, regardless of
    # whether the producer was imported or called elsewhere in the spec.
    assert options["datas"] == [
        ("data/catalog", "data/catalog"), *expected_tcl, *expected_metadata,
    ]
    assert requests == [(("watchdog",), {})]  # only the direct distribution
    # The real package must reach Analysis, not merely be mentioned/imported by
    # spec setup code. find_spec in doctor does not collect the module graph.
    assert options["hiddenimports"] == ["watchdog"]
    assert args == (["tools\\pyinstaller_entry.py"],)
    assert {key: value for key, value in options.items() if key != "datas"} == {
        "pathex": ["src"], "binaries": [], "hiddenimports": ["watchdog"], "hookspath": [],
        "hooksconfig": {}, "runtime_hooks": ["tools\\pyinstaller_tcl_runtime.py"],
        "excludes": [], "noarchive": False, "optimize": 0,
    }
    assert calls["pyz"] == ((analysis.pure,), {})
    assert calls["exe"] == ((pyz, analysis.scripts, analysis.binaries, analysis.datas, []), {
        "name": "PED-Hunter", "debug": False, "bootloader_ignore_signals": False,
        "strip": False, "upx": True, "upx_exclude": [], "runtime_tmpdir": ".",
        "console": True, "disable_windowed_traceback": False, "argv_emulation": False,
        "target_arch": None, "codesign_identity": None, "entitlements_file": None,
    })
