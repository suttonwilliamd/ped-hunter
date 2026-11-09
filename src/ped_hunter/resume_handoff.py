"""Explicit, one-use real-source cursor for a deliberate application cutover.

A claim is not completion evidence. It blocks another Resume until the operator
has verified normal ingestion/history conservation and archived that receipt.
Ordinary Resume-at-EOF is unchanged only when no handoff state exists.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


COST_SETUP_FIELDS = (
    "id", "name", "weapon", "amp", "scope", "sight_1", "sight_2",
    "damage_enhancers", "accuracy_enhancers", "economy_enhancers",
    "ammo_burn", "decay", "cost_per_shot",
)


def _paths(db_path: Path) -> tuple[Path, Path]:
    pending = Path(str(db_path) + ".resume.json")
    return pending, Path(str(pending) + ".claimed")


def has_resume_handoff(db_path: Path) -> bool:
    return any(path.exists() for path in _paths(db_path))


def _setup_context(setup: dict | None) -> dict | None:
    return {key: setup[key] for key in COST_SETUP_FIELDS} if setup is not None else None


def _boundary(stream, offset: int, last_line: str, *, full_prefix: bool = True) -> dict:
    if offset <= 0 or not last_line.strip():
        raise ValueError("Resume handoff needs a proven committed line and positive byte offset")
    stat = os.fstat(stream.fileno())
    if offset > stat.st_size:
        raise ValueError("Chat log was truncated after the resume checkpoint")
    start = max(0, offset - len(last_line.encode("utf-8")) - 1024)
    stream.seek(start)
    ending = stream.read(offset - start)
    if not ending.endswith(b"\n"):
        raise ValueError("Resume byte offset is not a complete raw newline boundary")
    observed = ending.split(b"\n")[-2].decode("utf-8-sig", errors="replace").strip()
    if observed != last_line.strip():
        raise ValueError("Resume byte offset does not end at the committed raw line")
    stream.seek(max(0, offset - 32))
    result = {"source_identity": [str(stat.st_dev), str(stat.st_ino)],
              "anchor_sha256": hashlib.sha256(stream.read(min(32, offset))).hexdigest()}
    if full_prefix:
        stream.seek(0)
        digest = hashlib.sha256()
        remaining = offset
        while remaining:
            chunk = stream.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("Chat log changed while hashing its committed prefix")
            digest.update(chunk)
            remaining -= len(chunk)
        result["source_prefix_sha256"] = digest.hexdigest()
    return result


def discover_resume_boundary(log_path: Path, committed_lines: list[str], *, max_bytes: int = 1024 * 1024) -> dict:
    """Find exactly one recent committed sequence in actual raw source order.

    Unparsed lines may intervene. Multiple identical occurrences/sequences are
    ambiguous: never choose the last text match, zero or EOF as a substitute.
    """
    expected = [line.strip() for line in committed_lines]
    if not expected or any(not line for line in expected):
        raise ValueError("Resume discovery needs a nonempty committed raw sequence")
    with log_path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        start = max(0, stat.st_size - max_bytes)
        stream.seek(start)
        data = stream.read(stat.st_size - start)
        if len(data) != stat.st_size - start:
            raise ValueError("Chat log changed during bounded boundary discovery")
        position = start
        ways = [1] + [0] * len(expected)
        unique_end = None
        for index, raw in enumerate(data.splitlines(keepends=True)):
            position += len(raw)
            if (start and index == 0) or not raw.endswith(b"\n"):
                continue
            text = raw.decode("utf-8-sig", errors="replace").strip()
            for length in range(len(expected), 0, -1):
                if text == expected[length - 1] and ways[length - 1]:
                    ways[length] = min(2, ways[length] + ways[length - 1])
                    if length == len(expected):
                        unique_end = position if ways[length] == 1 else None
        if ways[-1] != 1 or unique_end is None:
            raise ValueError("Committed raw sequence is missing or ambiguous in the bounded source window")
        return dict(_boundary(stream, unique_end, expected[-1]), offset=str(unique_end),
                    last_line=expected[-1], committed_lines=expected,
                    discovery_window_start=str(start), discovery_window_end=str(stat.st_size))


def stage_resume_handoff(db_path: Path, session_id: str, log_path: Path,
                         committed_lines: list[str], handoff_id: str, setup: dict | None) -> dict:
    """After old normal exit, stage a unique actual committed-source boundary."""
    pending, claimed = _paths(db_path)
    if pending.exists() or claimed.exists():
        raise ValueError("An earlier resume handoff requires reconciliation; never overwrite or replay it")
    data = dict(discover_resume_boundary(log_path, committed_lines), schema=1,
                handoff_id=handoff_id, session_id=session_id,
                log_path=str(log_path.resolve()), cost_setup=_setup_context(setup))
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
    return data


def validate_handoff_stream(stream, data: dict, *, full_prefix: bool = True) -> None:
    """Bind the actual opened reader file, not an earlier path-stat result."""
    observed = _boundary(stream, int(data["offset"]), data["last_line"], full_prefix=full_prefix)
    for key, value in observed.items():
        if value != data[key]:
            raise ValueError("Chat log identity/committed prefix changed after the resume checkpoint")


def read_resume_handoff(db_path: Path, session_id: str, log_path: Path,
                        setup: dict | None) -> dict | None:
    pending, claimed = _paths(db_path)
    if claimed.exists():
        raise ValueError("A previous handoff is claimed but not verified complete; reconcile its receipt before Resume")
    if not pending.exists():
        return None
    try:
        if pending.stat().st_size > 65536:
            raise ValueError("Resume handoff is too large")
        data = json.loads(pending.read_text(encoding="utf-8"))
        if data["schema"] != 1 or data["session_id"] != session_id:
            raise ValueError("Resume handoff belongs to a different session or schema")
        if Path(data["log_path"]).resolve() != log_path.resolve():
            raise ValueError("Resume handoff belongs to a different chat log")
        if not isinstance(data["offset"], str) or not data["offset"].isdigit():
            raise ValueError("Resume handoff byte offset must be a decimal string")
        if data["cost_setup"] != _setup_context(setup):
            raise ValueError("Selected cost setup changed after the resume checkpoint")
        with log_path.open("rb") as stream:
            validate_handoff_stream(stream, data)
        return data
    except (KeyError, TypeError) as exc:
        raise ValueError("Resume handoff is incomplete; preserve and reconcile it") from exc


def claim_resume_handoff(db_path: Path, expected: dict) -> None:
    """Exclusive claim before session mutation; uncertainty never auto-replays."""
    pending, claimed = _paths(db_path)
    if json.loads(pending.read_text(encoding="utf-8")) != expected:
        raise ValueError("Resume handoff changed before its claim")
    receipt = dict(expected, status="claimed; not completion proof",
                   claimed_utc=datetime.now(timezone.utc).isoformat())
    with claimed.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False)
    pending.unlink()
