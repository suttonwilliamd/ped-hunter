"""Real files and SQLite prove one-use cutover continuity, not a wiring echo."""
from dataclasses import asdict
import json
from pathlib import Path
import queue
import threading
from types import SimpleNamespace

import pytest

from ped_hunter.app import PedHunterApp, calculate_loadout_cost
from ped_hunter.catalog import Catalog
from ped_hunter.log_tail import LogTail
from ped_hunter.resume_handoff import (
    claim_resume_handoff, discover_resume_boundary, has_resume_handoff,
    read_resume_handoff, stage_resume_handoff,
)
from ped_hunter.storage import LoadoutRecord, Store


OLD = [
    "2026-11-09 01:00:00 [System] [] You inflicted 4.6 points of damage",
    "2026-11-09 01:00:01 [System] [] Your Médecine has improved by 0.25",
    "2026-11-09 01:00:02 [System] [] You received Iron Stone x (1) Value: 0.13 PED",
]
GAP = [
    "2026-11-09 01:01:00 [System] [] You missed",
    "2026-11-09 01:01:01 [System] [] Critical hit - Additional damage! You inflicted 17.4 points of damage",
    "2026-11-09 01:01:02 [System] [] You received Tailings x (318) Value: 0.0318 PED",
]
FUTURE = "2026-11-09 01:02:00 [System] [] You inflicted 4.6 points of damage"


def encoded(lines):
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def append(path, value):
    with path.open("ab") as stream:
        stream.write(value)


class Value:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class Polls:
    def __init__(self, actions):
        self.actions = iter(actions)
    def is_set(self):
        return False
    def wait(self, _timeout):
        action = next(self.actions, None)
        if action is None:
            return True
        action()
        return False


@pytest.fixture
def handoff_run(tmp_path, monkeypatch):
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / "run.sqlite3")
    app.catalog = Catalog.load()
    ammo, decay = calculate_loadout_cost(catalog=app.catalog, weapon_name="Basic Mining Laser, Type M (L)")
    setup = LoadoutRecord(id=None, name="Actual selected setup", weapon="Basic Mining Laser, Type M (L)",
                          ammo_burn=ammo, decay=decay, cost_per_shot=0.0223)
    setup.id = app.store.save_loadout(setup, make_active=True)
    sid = app.store.start_session("hunt", setup)
    path = tmp_path / "chat.log"
    path.write_bytes(encoded(OLD))
    app._log_queue = queue.Queue()
    app._process_lines(OLD, sid, None)
    with app.store.connect() as conn:
        original_rows = [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
    app.running = False
    app.chat_path = Value(str(path))
    app.status_text = Value()
    app._resume_session_id = lambda: sid
    app._refresh_all = lambda: None  # UI is incidental; the real Resume/store/worker run.
    app._schedule_log_drain = lambda: None
    app._log_worker = None
    app._log_worker_stop = threading.Event()
    app._log_drain_pending = False
    for name in ("start_button", "resume_button", "stop_button"):
        setattr(app, name, SimpleNamespace(configure=lambda **_kwargs: None))
    errors = []
    monkeypatch.setattr("ped_hunter.app.messagebox.showerror", lambda _title, message, **_kwargs: errors.append(message))
    context = asdict(app.store.get_active_loadout())
    plan = stage_resume_handoff(app.store.db_path, sid, path, OLD, "isolated-cutover-1", context)
    yield app, sid, path, plan, context, original_rows, errors
    app._stop_log_worker()


def test_unique_source_order_byte_boundary_handles_crlf_multibyte_and_ignored_lines(tmp_path):
    path = tmp_path / "chat.log"
    prefix = encoded([OLD[0], "2026-11-09 01:00:00 [System] [] Your tool tier increased", *OLD[1:]])
    path.write_bytes(prefix + encoded(GAP) + b"partial-\xc3")
    found = discover_resume_boundary(path, OLD)
    assert int(found["offset"]) == len(prefix)
    assert found["offset"] != str(len(prefix.decode("utf-8")))
    assert found["source_prefix_sha256"] == __import__("hashlib").sha256(prefix).hexdigest()
    assert found["anchor_sha256"] == __import__("hashlib").sha256(prefix[-32:]).hexdigest()


@pytest.mark.parametrize("data,sequence", [
    (encoded(OLD + OLD), OLD), (encoded([OLD[0], OLD[0]]), [OLD[0]]),
    (encoded(OLD), []), (encoded(OLD), [""]), (encoded(OLD), ["missing"]),
    (encoded(OLD).rstrip(b"\r\n"), OLD),
])
def test_ambiguous_missing_empty_or_partial_sequence_never_guesses_latest_or_eof(tmp_path, data, sequence):
    path = tmp_path / "chat.log"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        discover_resume_boundary(path, sequence)


def test_real_resume_books_install_downtime_and_future_once_without_touching_old_rows(handoff_run):
    app, sid, path, plan, context, old_rows, errors = handoff_run
    append(path, encoded(GAP))  # App absent/install period, before actual Resume.
    app.resume_selected_session()
    # Use the real thread's ingestion acknowledgment, not a sleep-as-proof.
    kind, count = app._log_queue.get(timeout=5)
    assert (kind, count) == ("parsed", 3)
    append(path, encoded([FUTURE]))
    assert app._log_queue.get(timeout=5) == ("parsed", 1)
    app._stop_log_worker()
    assert not errors
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == pytest.approx(4 * 0.0223)
    assert summary.loot_value == pytest.approx(0.1618)
    assert summary.repair_shots == 4
    with app.store.connect() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
        assert rows[:len(old_rows)] == old_rows
        assert [json.loads(r[-1]).get("outgoing_miss") for r in conn.execute("SELECT * FROM events WHERE raw_message=?", (GAP[0],))] == [True]
    assert len(rows) == len(old_rows) + 4
    assert has_resume_handoff(app.store.db_path)
    with pytest.raises(ValueError, match="claimed"):
        read_resume_handoff(app.store.db_path, sid, path, context)


@pytest.mark.parametrize("corruption", ["session", "setup", "prefix", "truncation", "replacement"])
def test_invalid_handoff_resume_leaves_session_setup_history_untouched(handoff_run, corruption):
    app, sid, path, plan, context, old_rows, errors = handoff_run
    marker = Path(str(app.store.db_path) + ".resume.json")
    if corruption == "session":
        data = json.loads(marker.read_text()); data["session_id"] = "another-session"; marker.write_text(json.dumps(data))
    elif corruption == "setup":
        data = json.loads(marker.read_text()); data["cost_setup"]["cost_per_shot"] = 999; marker.write_text(json.dumps(data))
    elif corruption == "prefix":
        raw = bytearray(path.read_bytes()); raw[0] = ord("9"); path.write_bytes(raw)
    elif corruption == "truncation":
        path.write_bytes(b"short\n")
    else:
        replacement = path.with_suffix(".new"); replacement.write_bytes(path.read_bytes()); replacement.replace(path)
    with app.store.connect() as conn:
        before = {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in ("sessions", "loadouts", "events")}
    app.resume_selected_session()
    assert not app.running and app._log_worker is None
    assert errors
    with app.store.connect() as conn:
        assert {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")] for table in before} == before
    assert marker.exists()


@pytest.mark.parametrize("when", ["before_constructor", "after_constructor", "after_read_before_ack"])
def test_actual_opened_reader_replacement_cannot_reset_to_zero_or_eof(handoff_run, when):
    app, sid, path, plan, context, _rows, _errors = handoff_run
    append(path, encoded(GAP))
    validated = read_resume_handoff(app.store.db_path, sid, path, context)
    tail = None if when == "before_constructor" else LogTail(path, int(plan["offset"]), handoff=validated)
    if when == "after_read_before_ack":
        assert tail.read_lines() == [s + "\r\n" for s in GAP]
    replacement = path.with_suffix(".new"); replacement.write_bytes(path.read_bytes()); replacement.replace(path)
    with pytest.raises(ValueError, match="changed|checkpoint"):
        if tail is None:
            LogTail(path, int(plan["offset"]), handoff=validated)
        elif when == "after_read_before_ack":
            tail.acknowledge_handoff()
        else:
            tail.read_lines()
    if tail is not None:
        assert tail.offset != 0


def test_failure_after_claim_before_worker_start_is_discoverable_and_never_eof(handoff_run):
    app, sid, path, plan, context, old_rows, _errors = handoff_run
    def fail_start():
        raise RuntimeError("simulated worker start failure")
    app._start_log_worker = fail_start
    with pytest.raises(RuntimeError, match="start failure"):
        app.resume_selected_session()
    with pytest.raises(ValueError, match="claimed"):
        read_resume_handoff(app.store.db_path, sid, path, context)
    with app.store.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id")] == old_rows


def test_partial_gap_commit_failure_halts_and_retains_receipt_without_replay(handoff_run, monkeypatch):
    app, sid, path, plan, context, old_rows, _errors = handoff_run
    append(path, encoded(GAP))
    original_add = app.store.add_event
    calls = 0
    def fail_second(session_id, event):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated write failure after first commit")
        return original_add(session_id, event)
    monkeypatch.setattr(app.store, "add_event", fail_second)
    app.resume_selected_session()
    app._log_worker.join(timeout=5)
    assert not app._log_worker.is_alive()
    kind, message = app._log_queue.get(timeout=1)
    assert kind == "error" and "incomplete" in message
    with app.store.connect() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
    assert rows[:len(old_rows)] == old_rows
    assert len(rows) == len(old_rows) + 1
    with pytest.raises(ValueError, match="claimed"):
        read_resume_handoff(app.store.db_path, sid, path, context)


def test_split_utf8_gap_and_legitimate_same_damage_at_new_timestamp_are_not_lost(handoff_run):
    app, sid, path, plan, context, old_rows, _errors = handoff_run
    text = "2026-11-09 01:01:00 [System] [] Your Médecine has improved by 0.5"
    raw = encoded([text]); split = raw.index("é".encode()) + 1
    append(path, raw[:split])
    loaded = read_resume_handoff(app.store.db_path, sid, path, context)
    claim_resume_handoff(app.store.db_path, loaded)
    stop = Polls([lambda: append(path, raw[split:] + encoded([FUTURE, FUTURE])), lambda: None])
    app._log_worker_loop(path, sid, int(loaded["offset"]), stop, loaded["last_line"], loaded)
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == pytest.approx(2 * 0.0223)
    assert summary.repair_shots == 2
    assert app.store.skill_gains_for_session(sid)[0].xp == pytest.approx(0.75)
    with app.store.connect() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM events ORDER BY id LIMIT ?", (len(old_rows),))] == old_rows


def test_pending_state_prevents_default_context_from_reactivating_a_stale_snapshot(handoff_run):
    app, _sid, _path, _plan, _context, _rows, _errors = handoff_run
    app._preferred_session = lambda: pytest.fail("Pending handoff must preserve current selected cost setup")
    app._restore_default_context()


def test_large_gap_cannot_release_binding_before_all_reader_chunks_commit(handoff_run):
    app, sid, path, plan, context, _rows, _errors = handoff_run
    append(path, b"ignored system output\n" * 55000 + encoded(GAP))
    loaded = read_resume_handoff(app.store.db_path, sid, path, context)
    tail = LogTail(path, int(loaded["offset"]), handoff=loaded)
    tail.read_lines()
    assert not tail.acknowledge_handoff()
    remaining = tail.read_lines()
    assert remaining[-3:] == [line + "\r\n" for line in GAP]
    assert tail.acknowledge_handoff()


def test_gap_uses_captured_price_then_future_uses_current_selected_setup(handoff_run):
    app, sid, path, plan, context, _rows, _errors = handoff_run
    append(path, encoded(GAP))
    loaded = read_resume_handoff(app.store.db_path, sid, path, context)
    claim_resume_handoff(app.store.db_path, loaded)
    other = LoadoutRecord(id=None, name="New future gear", weapon="Frontier Rifle",
                          ammo_burn=100, decay=0.0002, cost_per_shot=0.0102)
    other.id = app.store.save_loadout(other, make_active=False)
    def switch_and_append():
        app.store.set_active_loadout(other.id)
        append(path, encoded([FUTURE]))
    app._log_worker_loop(path, sid, int(loaded["offset"]), Polls([switch_and_append]),
                         loaded["last_line"], loaded)
    with app.store.connect() as conn:
        gap_payloads = [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM events WHERE raw_message IN (?,?) ORDER BY id", (GAP[0], GAP[1]))]
        future = json.loads(conn.execute("SELECT payload FROM events WHERE raw_message=?", (FUTURE,)).fetchone()[0])
    assert [p["shot_cost"] for p in gap_payloads] == pytest.approx([0.0223, 0.0223])
    assert future["shot_cost"] == pytest.approx(0.0102)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(3 * 0.0223 + 0.0102)
