"""Evidence-only recovery of a claimed cursor; never repair accounting or replay.

Inspection is schema-first SQLite read-only. Staging requires the ordinary GUI
OS lock shared by supported ingestion entry points, rechecks the full proof and advances
sidecars only. Accounting uncertainty is not an ingestion-integrity gate.
This operator path is deliberately separate from ordinary Resume-at-EOF.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .parser import parse_line
from .resume_handoff import (
    _boundary, _paths, _setup_context, validate_handoff_stream,
)

_BOOKKEEPING_KINDS = frozenset({'manufacturing_offset', 'manufacturing_allocation'})
_SOURCE_KINDS = frozenset({'chat', 'global', 'combat', 'loot', 'skill', 'craft',
                           'weapon', 'repair', 'equipment'})


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class ResumeReconciliation:
    session_id: str
    max_scan_bytes: int
    claimed_sha256: str
    anchor_event_id: int
    last_source_event_id: int
    global_last_event_id: int
    verified_source_rows: int
    preserved_bookkeeping_rows: int
    consecutive_duplicates: int
    ignored_source_lines: int
    rows_sha256: str
    checkpoint_json: str
    cost_setup_json: str

    @property
    def offset(self) -> str:
        return json.loads(self.checkpoint_json)['offset']


def inspect_resume_reconciliation(db_path: Path, log_path: Path, session_id: str, *,
                                  max_bytes: int = 8 * 1024 * 1024) -> ResumeReconciliation:
    """Prove every parsed line through the latest committed boundary, not EOF.

    Only the same sole unended run can acquire a successor cursor. An ended
    run, foreign later rows, missing/duplicate source records or a changed raw
    identity/prefix must be resolved separately. Uncommitted suffix lines are
    left to the existing one-use reader; they are never skipped or replayed by
    this function. Caller-visible row hashes contain no exported raw content.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= 64 * 1024 * 1024:
        raise ValueError('Recovery needs a positive bounded byte scope of at most64MiB')
    db_path, log_path = Path(db_path), Path(log_path)
    pending, claimed = _paths(db_path)
    if pending.exists():
        raise ValueError('A pending cursor already exists; preserve both sidecars and inspect them')
    if not claimed.is_file() or claimed.stat().st_size > 65536:
        raise ValueError('A bounded claimed receipt is required for reconciliation')
    raw_receipt = claimed.read_bytes()
    try:
        receipt = json.loads(raw_receipt)
        if not isinstance(receipt, dict):
            raise ValueError('Claimed receipt must be a structured object; preserve it')
        if not isinstance(receipt.get('last_line'), str) or not receipt['last_line'].strip():
            raise ValueError('Claimed receipt needs a nonempty raw anchor; preserve it')
        if receipt['schema'] != 1 or receipt['session_id'] != session_id:
            raise ValueError('Claimed receipt belongs to a different run or schema')
        if Path(receipt['log_path']).resolve() != log_path.resolve():
            raise ValueError('Claimed receipt belongs to a different chat log')
        if not isinstance(receipt['offset'], str) or not receipt['offset'].isdigit():
            raise ValueError('Claimed offset must be a decimal byte string')
        with log_path.open('rb') as stream:
            validate_handoff_stream(stream, receipt)
    except (KeyError, TypeError) as exc:
        raise ValueError('Claimed receipt is incomplete; preserve it') from exc

    with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        schema = {row['name'] for row in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table'")}
        if not {'events', 'sessions', 'loadouts'} <= schema:
            raise ValueError('Resume database schema is missing required tables')
        conn.execute('BEGIN')
        empty_start = (receipt.get('boundary_kind') == 'captured-empty-run-start'
                       and receipt.get('committed_lines') == [])
        global_before = int(conn.execute('SELECT COALESCE(max(id),0) FROM events').fetchone()[0])
        active = conn.execute('SELECT id FROM sessions WHERE ended_at IS NULL').fetchall()
        if [row['id'] for row in active] != [session_id]:
            raise ValueError('Only the same sole unended run can acquire a successor cursor')
        if empty_start:
            # The cursor was captured before this run committed anything, so the
            # run start is the anchor: EVERY row of the run must match the source
            # interval from the receipt offset, in order (proved below).
            first = conn.execute('SELECT min(id) FROM events WHERE session_id=?',
                                 (session_id,)).fetchone()[0]
            anchor = dict(id=(int(first) - 1) if first is not None else global_before,
                          boundary_kind='captured-empty-run-start', offset=receipt['offset'],
                          last_line=receipt['last_line'])
        else:
            anchors = conn.execute(
                'SELECT * FROM events WHERE session_id=? AND raw_message=? ORDER BY id',
                (session_id, receipt['last_line'])).fetchall()
            if len(anchors) != 1:
                raise ValueError('Claimed committed raw anchor is missing or ambiguous in this run')
            anchor = anchors[0]
            parsed_anchor = parse_line(anchor['raw_message'])
            if parsed_anchor is None or parsed_anchor.kind != anchor['kind']:
                raise ValueError('Claimed anchor is not a committed source event')
        if conn.execute('SELECT 1 FROM events WHERE id>? AND session_id!=? LIMIT 1',
                        (anchor['id'], session_id)).fetchone():
            raise ValueError('Later rows belong to another run; do not replay its suffix here')
        rows = conn.execute('SELECT * FROM events WHERE session_id=? AND id>? ORDER BY id',
                            (session_id, anchor['id'])).fetchall()
        source_rows, bookkeeping = [], 0
        for row in rows:
            if row['kind'] in _BOOKKEEPING_KINDS:
                bookkeeping += 1
                continue
            parsed = parse_line(row['raw_message'])
            if row['kind'] not in _SOURCE_KINDS or parsed is None or parsed.kind != row['kind']:
                raise ValueError('Unrecognized committed event provenance; preserve and inspect it')
            source_rows.append(row)
        setup_rows = conn.execute('SELECT * FROM loadouts WHERE active=1').fetchall()
        if len(setup_rows) > 1:
            raise ValueError('Multiple active cost setups are ambiguous')
        setup = _setup_context(dict(setup_rows[0])) if setup_rows else None
        global_last_id = int(conn.execute('SELECT COALESCE(max(id),0) FROM events').fetchone()[0])
        rows_digest = hashlib.sha256(_json(
            [dict(anchor)] + [dict(row) for row in rows]).encode('utf-8')).hexdigest()

    start = end = int(receipt['offset'])
    expected_rows = [(row['raw_message'].strip(), row['kind']) for row in source_rows]
    previous = receipt['last_line'].strip()
    parsed_count = duplicates = ignored = 0
    with log_path.open('rb') as stream:
        validate_handoff_stream(stream, receipt)
        if source_rows:
            stream.seek(start)
            interval = stream.read(max_bytes)
            # Starting at a PROVEN byte anchor makes the reader deterministic.
            # Stop only after every committed source row matches in order; a
            # repeated text subsequence is not a substitute for this proof.
            for part in interval.split(b'\n')[:-1]:
                raw = part + b'\n'
                end += len(raw)
                line = part.decode('utf-8-sig', errors='replace').strip()
                if line == previous:
                    duplicates += 1
                    continue
                event = parse_line(line)
                if event is None:
                    ignored += 1
                    continue
                if (line, event.kind) != expected_rows[parsed_count]:
                    raise ValueError('Committed source interval has a missing/extra/reordered parsed event')
                parsed_count += 1
                previous = line
                if parsed_count == len(expected_rows):
                    break
            if parsed_count != len(expected_rows):
                raise ValueError('Committed source rows are missing within bounded recovery scope')
            scanned_span = interval[:end - start]
            checkpoint = dict(_boundary(stream, end, previous), offset=str(end),
                              last_line=previous,
                              committed_lines=[row['raw_message'] for row in source_rows[-8:]])
            stream.seek(start)
            if stream.read(end - start) != scanned_span:
                raise ValueError('Committed raw span changed between scan and checkpoint hashing')
            validate_handoff_stream(stream, checkpoint)
        else:
            checkpoint = {key: receipt[key] for key in (
                'source_identity', 'anchor_sha256', 'source_prefix_sha256', 'offset', 'last_line')}
            checkpoint['committed_lines'] = receipt.get('committed_lines', [receipt['last_line']])
        # Detect a rewrite of the original prefix during the bounded scan too.
        validate_handoff_stream(stream, receipt)
    if claimed.read_bytes() != raw_receipt:
        raise ValueError('Claimed receipt changed during reconciliation')
    # Discovery-window size can grow without changing a proven committed prefix.
    checkpoint = {key: value for key, value in checkpoint.items()
                  if key not in ('discovery_window_start', 'discovery_window_end')}
    return ResumeReconciliation(
        session_id, max_bytes, hashlib.sha256(raw_receipt).hexdigest(), int(anchor['id']),
        int(source_rows[-1]['id']) if source_rows else int(anchor['id']),
        global_last_id, len(source_rows), bookkeeping, duplicates, ignored,
        rows_digest, _json(checkpoint), _json(setup))


def reconciliation_summary(evidence: ResumeReconciliation) -> dict:
    """Sanitized operator receipt; bind confirmation to all private proof fields."""
    return dict(schema=1, session_id=evidence.session_id,
                proof_sha256=hashlib.sha256(_json(asdict(evidence)).encode('utf-8')).hexdigest(),
                max_scan_bytes=evidence.max_scan_bytes,
                anchor_event_id=evidence.anchor_event_id,
                last_source_event_id=evidence.last_source_event_id,
                verified_source_rows=evidence.verified_source_rows,
                preserved_bookkeeping_rows=evidence.preserved_bookkeeping_rows,
                consecutive_duplicates=evidence.consecutive_duplicates,
                ignored_source_lines=evidence.ignored_source_lines,
                offset=evidence.offset, accounting_changed=False)


def stage_reconciled_resume(db_path: Path, log_path: Path, expected: ResumeReconciliation,
                             handoff_id: str) -> Path:
    """Explicit operator action with the real GUI lock; no DB/log/accounting writes.

    Staging is process-exception-safe: the old claim remains a blocker until
    a complete successor file has been fsynced. An interrupted publication
    leaves guards; ordinary Resume also recognizes an orphaned archive rather
    than silently falling back to EOF if successor metadata is lost. This is
    not a guarantee against arbitrary filesystem corruption or power loss.
    All supported ingestion entry points share the OS owner lock; external
    programs writing the DB directly must still be quiescent.
    Never delete a guard to force progress.
    The returned archive preserves the original claimed receipt bytes verbatim.
    """
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', handoff_id):
        raise ValueError('A short filesystem-safe handoff ID is required')
    from .cli import _single_gui_instance
    with _single_gui_instance():
        observed = inspect_resume_reconciliation(db_path, log_path, expected.session_id,
                                                 max_bytes=expected.max_scan_bytes)
        if observed != expected:
            raise ValueError('Resume evidence changed; inspect a fresh snapshot before staging')
        pending, claimed = _paths(Path(db_path))
        old_bytes = claimed.read_bytes()
        if hashlib.sha256(old_bytes).hexdigest() != expected.claimed_sha256:
            raise ValueError('Claimed receipt changed before staging')
        successor = dict(json.loads(expected.checkpoint_json), schema=1,
                         handoff_id=handoff_id, session_id=expected.session_id,
                         log_path=str(Path(log_path).resolve()),
                         cost_setup=json.loads(expected.cost_setup_json),
                         reconciled_from_sha256=expected.claimed_sha256,
                         reconciliation='exact committed-source interval; accounting unchanged')
        encoded = json.dumps(successor, ensure_ascii=False).encode('utf-8')
        # The ordinary reader subsequently adds a status/timestamp on claim;
        # keep that next receipt recoverable under the same bounded format too.
        claimed_size = len(json.dumps(dict(successor,
            status='claimed; not completion proof',
            claimed_utc='9999-12-31T23:59:59.999999+00:00'), ensure_ascii=False).encode('utf-8'))
        if max(len(encoded), claimed_size) > 65536:
            raise ValueError('Successor receipt exceeds the bounded Resume format; original claim preserved')
        with pending.open('xb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # The claim remains a guard if this final recheck/archive fails.
        if claimed.read_bytes() != old_bytes:
            raise ValueError('Claim changed during staging; preserve both sidecars')
        archive = Path(str(claimed) + '.verified-' + uuid.uuid4().hex)
        claimed.rename(archive)
        return archive
