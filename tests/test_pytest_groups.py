"""Exercise the real CLI/pytest collector on disposable repositories; no app GUI."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "pytest_groups.py"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tests").mkdir()
    shutil.copyfile(SCRIPT, tmp_path / "tools" / SCRIPT.name)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\naddopts = -q\n", encoding="utf-8")
    return tmp_path


def module(project, filename, cases=("selected",), *, failing=()):
    source = ["from pathlib import Path\n"]
    for case in cases:
        source.extend([
            f"def test_{case}():\n",
            "    with Path('executed.txt').open('a', encoding='utf-8') as log:\n",
            f"        log.write({case!r} + '\\n')\n",
        ])
        if case in failing:
            source.append("    assert False, 'deliberate selected failure'\n")
    (project / "tests" / filename).write_text("".join(source), encoding="utf-8")


def command(project, *args):
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)  # Only these disposable children are isolated.
    return subprocess.run([sys.executable, str(project / "tools" / SCRIPT.name), *args],
                          cwd=project.parent, env=env, capture_output=True, text=True, timeout=45)


def executed(project):
    path = project / "executed.txt"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_unknown_group_fails_before_importing_test_modules(project):
    (project / "tests" / "test_parser.py").write_text(
        "from pathlib import Path\nPath('imported.txt').touch()\n", encoding="utf-8")
    result = command(project, "run", "misspelled-group")
    assert result.returncode == 2
    assert "misspelled-group" in result.stderr and "chart-accounting" in result.stderr
    assert not (project / "imported.txt").exists()


def test_explicit_group_is_required_instead_of_implicitly_running_everything(project):
    module(project, "test_parser.py")
    result = command(project, "run")
    assert result.returncode == 2
    assert executed(project) == []


def test_overlapping_and_repeated_groups_execute_each_case_once(project):
    module(project, "test_incremental_trend.py")
    module(project, "test_catalog.py", ("not_selected",), failing=("not_selected",))
    result = command(project, "run", "ingestion", "chart-accounting", "ingestion")
    assert result.returncode == 0, result.stdout + result.stderr
    assert executed(project) == ["selected"]


def test_dry_run_reports_selected_scope_without_executing_bodies(project):
    module(project, "test_parser.py")
    module(project, "test_catalog.py", ("catalog",))
    result = command(project, "run", "ingestion", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tests/test_parser.py" in result.stdout
    assert "tests/test_catalog.py" not in result.stdout
    assert executed(project) == []


def test_list_reports_overlaps_and_unmapped_files_without_executing(project):
    module(project, "test_incremental_trend.py")
    module(project, "test_future_component.py", ("future",))
    result = command(project, "list")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "chart-accounting" in result.stdout and "ingestion" in result.stdout
    assert "UNMAPPED: tests/test_future_component.py" in result.stdout
    assert executed(project) == []


def test_unmapped_file_blocks_focused_execution_without_a_full_fallback(project):
    module(project, "test_parser.py")
    module(project, "test_future_component.py", ("future",))
    result = command(project, "run", "ingestion")
    assert result.returncode != 0
    assert "test_future_component.py" in result.stdout + result.stderr
    assert executed(project) == []


def test_collection_skipped_unmapped_file_is_still_surfaced(project):
    module(project, "test_parser.py")
    (project / "tests" / "test_future_component.py").write_text(
        "import pytest\npytest.skip('unavailable optional environment', allow_module_level=True)\n",
        encoding="utf-8")
    result = command(project, "run", "ingestion", "--dry-run")
    assert result.returncode != 0
    assert "test_future_component.py" in result.stdout + result.stderr
    assert executed(project) == []


def test_full_mode_includes_unmapped_files_and_keeps_default_discovery(project):
    module(project, "test_parser.py")
    module(project, "test_future_component.py", ("future",))
    result = command(project, "full", "--", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(executed(project)) == ["future", "selected"]
    assert "UNMAPPED: tests/test_future_component.py" in result.stdout


def test_custom_pytest_filename_discovery_is_not_silently_excluded(project):
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\npython_files = checks_*.py\n", encoding="utf-8")
    module(project, "checks_future.py", ("custom",))
    focused = command(project, "run", "ingestion", "--dry-run")
    assert focused.returncode != 0
    assert "checks_future.py" in focused.stdout + focused.stderr
    assert executed(project) == []
    full = command(project, "full")
    assert full.returncode == 0, full.stdout + full.stderr
    assert executed(project) == ["custom"]


@pytest.mark.parametrize("mode,options", [
    ("full", ("-k", "selected")),
    ("full", ("-m", "selected")),
    ("run", ("--ignore", "tests/test_parser.py")),
    ("run", ("--lf",)),
    ("run", ("tests/test_parser.py",)),
])
def test_narrowing_discovery_cannot_hide_tests_or_masquerade_as_full(project, mode, options):
    module(project, "test_parser.py")
    prefix = ("run", "ingestion") if mode == "run" else ("full",)
    result = command(project, *prefix, "--", *options)
    assert result.returncode != 0
    assert executed(project) == []


def test_selected_failure_propagates_as_failure(project):
    module(project, "test_parser.py", failing=("selected",))
    result = command(project, "run", "ingestion")
    assert result.returncode == 1, result.stdout + result.stderr
    assert executed(project) == ["selected"]


def test_isolated_mode_deduplicates_cases_and_propagates_child_failures(project):
    module(project, "test_incremental_trend.py", ("selected", "broken"), failing=("broken",))
    result = command(project, "run", "ingestion", "chart-accounting", "--isolated", "--", "-q")
    assert result.returncode == 1, result.stdout + result.stderr
    assert executed(project) == ["selected", "broken"]
