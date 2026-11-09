"""Opt-in pytest selection; ordinary pytest discovery and CI remain unchanged."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from fnmatch import fnmatchcase
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Group:
    purpose: str
    files: tuple[str, ...]


# File-level overlap is intentional: e.g. loadouts exercises parser -> Store ->
# displayed costs, not just catalog records. See docs/testing.md for boundaries.
GROUPS = {
    "chart-accounting": Group("Booked input, inferred groups, estimates, fans and chart consumers", (
        "test_activity_trend.py", "test_incremental_trend.py", "test_hunt_attribution*.py",
        "test_chart_*.py", "test_loadouts.py", "test_storage_repair_cache.py",
        "test_streamer_crafting.py", "test_streamer_redesign.py", "test_streamer_refresh.py",
        "test_live_refresh_budget.py", "test_space_mining*.py", "test_resume_handoff.py")),
    "ingestion": Group("Parser, file watching, resume boundaries and charged-event consumers", (
        "test_parser.py", "test_log_ingestion.py", "test_log_watcher.py",
        "test_default_log_path.py", "test_loadouts.py", "test_incremental_trend.py",
        "test_live_refresh_budget.py", "test_session_lifecycle.py",
        "test_space_mining*.py", "test_resume_handoff.py", "test_resume_responsiveness.py")),
    "catalog-loadouts": Group("Catalog imports, ammo/decay, repair budgets, currency and setup snapshots", (
        "test_catalog.py", "test_loadouts.py", "test_setup_editor.py", "test_currency.py",
        "test_storage_repair_cache.py", "test_resume_default.py", "test_space_mining*.py")),
    "persistence-lifecycle": Group("Stored events/estimates/fans, session resume and instance ownership", (
        "test_session_lifecycle.py", "test_resume_default.py", "test_storage_repair_cache.py",
        "test_hunt_attribution_storage.py", "test_chart_trails*.py", "test_log_ingestion.py",
        "test_loadouts.py", "test_setup_editor.py", "test_single_instance.py", "test_dashboard_startup.py",
        "test_resume_handoff.py", "test_resume_responsiveness.py", "test_space_mining*.py")),
    "desktop-ui": Group("Native layout, DPI, feedback, streamer behavior and live UI refresh", (
        "test_dashboard_startup.py", "test_default_layout.py", "test_setup_editor.py", "test_dpi.py",
        "test_feedback.py", "test_feedback_sounds.py", "test_streamer_*.py",
        "test_chart_trails_ui.py", "test_log_ingestion.py", "test_live_refresh_budget.py",
        "test_default_log_path.py", "test_space_mining_ui.py", "test_resume_responsiveness.py")),
    "packaging-diagnostics": Group("CLI/doctor, frozen helper/spec, version and test-tooling contracts", (
        "test_doctor.py", "test_frozen_doctor_smoke.py", "test_packaging_spec.py",
        "test_single_instance.py", "test_catalog.py", "test_feedback_sounds.py",
        "test_pytest_groups.py")),
    "trading": Group("WTB extraction, trade-channel filtering and stored/displayed listings", (
        "test_wtb.py", "test_parser.py")),
}


def groups_for(path: str) -> tuple[str, ...]:
    return tuple(name for name, group in GROUPS.items()
                 if any(fnmatchcase(path, "tests/" + pattern) for pattern in group.files))


class Inventory:
    """Observe pytest's real collectors, including skipped files and custom naming."""

    def __init__(self, groups: tuple[str, ...] | None, *, full: bool = False):
        self.groups = groups
        self.full = full
        self.files: dict[str, tuple[str, ...]] = {}
        self.nodes: list[str] = []
        self.counts: Counter[str] = Counter()
        self.skips: list[str] = []

    def pytest_configure(self, config):
        self.root = config.rootpath
        # Do not let file-level shortcuts hide unmapped tests from collection.
        for option in ("file_or_dir", "ignore", "ignore_glob", "lf", "stepwise"):
            if config.getoption(option, default=None):
                raise pytest.UsageError(f"Group tooling cannot narrow discovery with {option}; use ordinary pytest for explicit subsets.")
        if self.full:
            for option in ("keyword", "markexpr", "deselect"):
                if config.getoption(option, default=None):
                    raise pytest.UsageError(f"Full mode cannot narrow selection with {option}; use run GROUP instead.")

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def pytest_collectstart(self, collector):
        if isinstance(collector, pytest.File):
            path = self.relative(collector.path)
            self.files[path] = groups_for(path)

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skips.append(f"{report.nodeid}: {report.longrepr}")

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        for item in items:
            path = self.relative(item.path)
            self.files.setdefault(path, groups_for(path))
        if self.groups is not None:
            if self.unmapped:
                raise pytest.UsageError("Unmapped test files: " + ", ".join(self.unmapped)
                                        + ". Assign their groups; full mode still includes them. No automatic full fallback.")
            wanted = set(self.groups)
            selected, excluded = [], []
            for item in items:
                (selected if wanted.intersection(self.files[self.relative(item.path)]) else excluded).append(item)
            config.hook.pytest_deselected(items=excluded)
            items[:] = selected  # One pass: overlapping/repeated groups never duplicate cases.
        self.nodes = [item.nodeid for item in items]
        self.counts = Counter(self.relative(item.path) for item in items)

    @property
    def unmapped(self) -> list[str]:
        return sorted(path for path, groups in self.files.items() if not groups)

    def report(self):
        wanted = set(self.groups) if self.groups is not None else None
        files = [path for path, groups in self.files.items()
                 if wanted is None or wanted.intersection(groups)]
        print(f"Selection: {len(self.nodes)} test cases in {len(files)} files (collection, not pass evidence)")
        for path in sorted(files):
            print(f"  {path}: {self.counts[path]} selected [{', '.join(self.files[path]) or 'UNMAPPED'}]")
        for path in self.unmapped:
            print(f"UNMAPPED: {path} (included by full mode; focused runs refuse it)")
        for reason in self.skips:
            print(f"COLLECTION SKIP: {reason}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    split = argv.index("--") if "--" in argv else len(argv)
    own, extra = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(description=__doc__, epilog="Pass pytest options after --. See docs/testing.md.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Collect and list groups, files, skips and unmapped files")
    focused = commands.add_parser("run", help="Run an explicit union of groups")
    focused.add_argument("groups", nargs="+", choices=tuple(GROUPS))
    complete = commands.add_parser("full", help="Run ordinary complete pytest discovery, including unmapped files")
    for command in (focused, complete):
        command.add_argument("--dry-run", action="store_true", help="Collect/report only; do not execute test bodies")
        command.add_argument("--isolated", action="store_true", help="Use a fresh pytest process for each case (not GUI monitor isolation)")
    args = parser.parse_args(own)
    if args.command == "list" and extra:
        parser.error("list takes no pytest filters; use run GROUP --dry-run -- OPTIONS")
    groups = tuple(dict.fromkeys(args.groups)) if args.command == "run" else None
    inventory = Inventory(groups, full=args.command == "full")
    collect = args.command == "list" or args.dry_run or args.isolated
    pytest_args = ["--collect-only", *extra] if collect else extra
    old_cwd = Path.cwd()
    try:
        os.chdir(ROOT)
        if args.command == "list":
            for name, group in GROUPS.items():
                print(f"{name}: {group.purpose}")
        else:
            print(("FULL discovery" if groups is None else "FOCUSED: " + ", ".join(groups))
                  + (" (collection only)" if args.dry_run else ""), flush=True)
        if collect:
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                result = int(pytest.main(pytest_args, plugins=[inventory]))
            if result:
                print(output.getvalue(), file=sys.stderr, end="")
        else:
            result = int(pytest.main(pytest_args, plugins=[inventory]))
        inventory.report()
        if result or args.command == "list" or args.dry_run or not args.isolated:
            return result
        failures = 0
        for node in inventory.nodes:
            print(f"ISOLATED: {node}", flush=True)
            failures += subprocess.call([sys.executable, "-m", "pytest", node, *extra], cwd=ROOT) != 0
        print(f"Isolated pytest processes: {len(inventory.nodes)}; failed processes: {failures}. Review each pytest summary for skips.")
        return int(bool(failures))
    finally:
        os.chdir(old_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
