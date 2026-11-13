"""Synthetic log -> immutable expenses -> conservative encounter projections."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
import math
import sqlite3

import pytest

from ped_hunter.app import _kill_trend_points
from ped_hunter.catalog import Catalog, HealingToolRecord
from ped_hunter.chart_trails import TrailRecorder
from ped_hunter.hunt_attribution import HuntAttribution
from ped_hunter.storage import Store
from test_healing import FAP, rows, worker

HOT = "Synthetic HoT"
MARKER = "Received Effect Over Time: Heal"
BASE = 0.00651


@pytest.fixture
def dual(worker):
    app, _sid = worker
    app.catalog.healing_tools[HOT] = HealingToolRecord(
        HOT, .1, source_name="Synthetic fixture", source_url="https://example.invalid/hot",
        heal_min=100, heal_max=120, uses_per_minute=2, has_hot=True,
        hot_fraction=.5, hot_duration=30, hot_trigger="use",
    )
    app.store.set_selected_hot_tool(HOT)
    return worker


def message(second, text):
    stamp = datetime(2026, 11, 8, 10) + timedelta(seconds=second)
    return f"{stamp:%Y-%m-%d %H:%M:%S} [System] [] {text}"


def send(run, second, text):
    app, sid = run
    app._process_lines([message(second, text)], sid, None)


def project(run, engine=None, recorder=None):
    app, sid = run
    points = _kill_trend_points(app.store, sid)
    if engine:
        points = engine.update(sid, points, live=True)
    if recorder:
        recorder.update(sid, points, live=True,
                        revised_event_ids=getattr(points, "attribution_changed_ids", ()))
    return points


def assert_conserved(run, points):
    summary = run[0].store.get_session(run[1])
    assert points.healing_allocated + points.healing_pending == pytest.approx(summary.healing_cost)
    assert math.fsum(p["cost"] or 0 for p in points) + points.attribution_pending == pytest.approx(summary.hunting_cost)
    assert math.fsum(p["loot"] for p in points) == pytest.approx(summary.loot_value)
    assert math.fsum(p["profit"] or 0 for p in points) - points.attribution_pending == pytest.approx(summary.net_value)


def test_one_hot_charge_routes_to_upcoming_loot_and_never_to_later_ticks(dual):
    app, sid = dual
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, MARKER)
    send(dual, 1, "You healed yourself 55.0 points")
    for second in (3, 5, 7):
        send(dual, second, "You healed yourself 8.0 points")
    before_loot = project(dual)
    assert before_loot[0]["cost"] == pytest.approx(BASE)
    assert before_loot.healing_pending == pytest.approx(.1)
    send(dual, 8, "You received Shrapnel x (100) Value: 1.0 PED")
    points = project(dual)
    assert points[0]["weapon_cost"] == pytest.approx(BASE)
    assert points[0]["healing_cost"] == pytest.approx(.1)
    assert points[0]["cost"] == pytest.approx(BASE + .1)
    assert points[0]["avg_cost"] == pytest.approx(BASE + .1)
    send(dual, 10, "You healed yourself 8.0 points")
    assert project(dual) == points
    summary = app.store.get_session(sid)
    assert (summary.estimated_healing_uses, summary.healing_cost) == (1, .1)
    assert app.store.lifetime_totals().total_cost == pytest.approx(BASE + .1)
    assert_conserved(dual, project(dual))


def test_hot_recipient_uses_marker_context_not_subsequent_combat_or_tick(dual):
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, "You received Shrapnel x (100) Value: 1.0 PED")
    send(dual, 4, MARKER)  # Between fights: previous receipt.
    send(dual, 4, "You inflicted 6.0 points of damage")
    send(dual, 4, "You healed yourself 55.0 points")
    send(dual, 6, "You received Shrapnel x (100) Value: 1.0 PED")
    send(dual, 8, "You healed yourself 8.0 points")
    points = project(dual)
    assert [p["healing_cost"] for p in points] == pytest.approx([.1, 0])
    assert [p["cost"] for p in points] == pytest.approx([BASE + .1, BASE])
    assert points[-1]["avg_cost"] == pytest.approx(BASE + .05)
    assert_conserved(dual, points)


def test_selection_restart_and_run_switch_keep_activation_price_snapshot(dual):
    app, first_sid = dual
    second_name = "Synthetic second HoT"
    app.catalog.healing_tools[second_name] = replace(app.catalog.healing_tools[HOT], name=second_name, decay=.2)
    send(dual, 0, MARKER)
    app.store.set_selected_hot_tool(second_name)
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], decay=9)
    app.store = Store(app.store.db_path)
    send(dual, 0, "You healed yourself 55.0 points")
    first_paid = next(row for row in rows(dual) if "healing_cost" in row["payload"])
    assert first_paid["payload"]["healing_cost"] == .1
    assert first_paid["payload"]["healing_tool"] == HOT
    app.store.end_session(first_sid)
    second_sid = app.store.start_session("hunt", app.store.get_active_loadout())
    send((app, second_sid), 10, MARKER)
    send((app, second_sid), 10, "You healed yourself 55.0 points")
    app.store.end_session(second_sid)
    app.store = Store(app.store.db_path)
    assert app.store.get_selected_hot_tool() == second_name
    app.store.resume_session(first_sid)
    send(dual, 12, "You healed yourself 8.0 points")
    assert app.store.get_session(first_sid).healing_cost == .1
    assert app.store.get_session(second_sid).healing_cost == .2
    assert app.store.lifetime_totals().total_cost == pytest.approx(.3)
    assert next(r for r in rows(dual) if r["id"] == first_paid["id"]) == first_paid


def test_full_replay_after_restart_never_recharges_or_reprices_cast(dual):
    app, sid = dual
    original = [(0, MARKER), (0, "You healed yourself 55.0 points"), (2, "You healed yourself 8.0 points")]
    for second, text in original:
        send(dual, second, text)
    retained = rows(dual)
    app.store = Store(app.store.db_path)
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], decay=5)
    for second, text in original:
        send(dual, second, text)
    assert app.store.get_session(sid).healing_cost == .1
    assert app.store.get_session(sid).estimated_healing_uses == 1
    assert rows(dual)[:len(retained)] == retained
    assert all("healing_cost" not in row["payload"] for row in rows(dual)[len(retained):])


@pytest.mark.parametrize("sequence", [
    [(0, MARKER), (0, "You healed yourself 0.0 points"), (1, "You healed yourself 55.0 points")],
    [(0, MARKER), (0, "You healed yourself 1.0 points"), (1, "You healed yourself 55.0 points")],
    [(0, MARKER), (3, "You healed yourself 55.0 points")],
    [(0, "You healed yourself 55.0 points")],
    [(0, "You were healed 30.0 points by Synthetic Avatar"), (0, MARKER), (0, "You healed yourself 55.0 points")],
    [(0, MARKER), (0, "You were healed 30.0 points by Synthetic Avatar"), (0, "You healed yourself 55.0 points")],
    [(0, MARKER), (1, MARKER), (1, "You healed yourself 55.0 points")],
    [(0, MARKER), (0, "Healing is diminished while moving"), (0, "You healed yourself 55.0 points")],
])
def test_ambiguous_capped_external_missing_and_delayed_evidence_stays_unpriced(dual, sequence):
    for second, text in sequence:
        send(dual, second, text)
    send(dual, 4, "You healed yourself 8.0 points")
    summary = dual[0].store.get_session(dual[1])
    assert summary.healing_cost == 0
    assert summary.estimated_healing_uses == 0
    assert all("healing_cost" not in row["payload"] for row in rows(dual))


def test_mixed_tools_charge_only_when_amounts_are_disjoint_without_tick_calibration(dual):
    app, sid = dual
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], heal_min=100, heal_max=100, hot_fraction=.1)
    app.store.set_selected_healing_tool(FAP)
    send(dual, 0, MARKER)
    send(dual, 0, "You healed yourself 90.0 points")
    send(dual, 1, "You healed yourself 23.0 points")  # Above the entire remaining HoT budget, not a guessed tick size.
    send(dual, 2, "You healed yourself 5.0 points")
    summary = app.store.get_session(sid)
    assert summary.healing_cost == pytest.approx(.12)
    assert summary.estimated_healing_uses == 2
    assert [r["payload"]["healing_kind"] for r in rows(dual) if "healing_cost" in r["payload"]] == ["hot", "instant"]


@pytest.mark.parametrize("changes", [
    {"hot_fraction": None}, {"hot_duration": None}, {"hot_trigger": "equip"},
    {"uses_per_minute": 120}, {"uses_per_minute": None}, {"decay": None},
])
def test_unknown_passive_or_indistinguishable_profile_cannot_become_a_paid_tick(dual, changes):
    app, sid = dual
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], **changes)
    send(dual, 0, MARKER)
    send(dual, 0, "You healed yourself 55.0 points")
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], hot_fraction=.5, hot_duration=30,
                                            hot_trigger="use", uses_per_minute=2, decay=.1)
    app.store = Store(app.store.db_path)
    send(dual, 2, "You healed yourself 55.0 points")
    assert app.store.get_session(sid).healing_cost == 0
    assert app.store.get_session(sid).estimated_healing_uses == 0


def test_overlap_retains_longer_old_duration_across_restart_and_selection_clear(dual):
    app, sid = dual
    send(dual, 0, MARKER)
    send(dual, 0, "You healed yourself 55.0 points")
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], hot_duration=5)
    send(dual, 10, MARKER)
    send(dual, 10, "You healed yourself 55.0 points")
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    app.store = Store(app.store.db_path)
    send(dual, 16, "You healed yourself 23.0 points")
    assert app.store.get_session(sid).healing_cost == .1
    send(dual, 31, "You healed yourself 23.0 points")
    assert app.store.get_session(sid).healing_cost == pytest.approx(.12)


def test_secondary_healing_is_booked_once_but_has_no_invented_recipient(dual):
    app, sid = dual
    app.catalog.healing_tools[HOT] = replace(app.catalog.healing_tools[HOT], secondary_healing=True)
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, MARKER)
    send(dual, 1, "You healed yourself 55.0 points")
    send(dual, 2, "You received Shrapnel x (100) Value: 1.0 PED")
    points = project(dual)
    assert points[0]["cost"] == pytest.approx(BASE)
    assert points.healing_pending == .1
    assert any("secondary" in reason for reason in points.healing_pending_reasons)
    assert_conserved(dual, points)


def test_legacy_expense_stays_unassigned_while_future_instant_heal_uses_previous_loot(dual):
    app, sid = dual
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, "You received Shrapnel x (100) Value: 1.0 PED")
    app.store.add_event(sid, {"kind": "combat", "timestamp": "2026-11-08T10:00:02",
        "raw_message": "synthetic preserved legacy heal", "payload": {"healed": 23.0, "healing_cost": .02}})
    legacy = rows(dual)[-1]
    send(dual, 4, "You healed yourself 23.0 points")
    points = project(dual)
    assert len(points) == 1
    assert points[0]["cost"] == pytest.approx(BASE + .02)
    assert points.healing_pending == pytest.approx(.02)
    assert next(r for r in rows(dual) if r["id"] == legacy["id"]) == legacy
    assert_conserved(dual, points)


def test_incoming_combat_without_outgoing_boundary_does_not_retarget_previous_loot(dual):
    app, _sid = dual
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, "You received Shrapnel x (100) Value: 1.0 PED")
    send(dual, 3, "You took 3.0 points of damage")
    send(dual, 4, "You healed yourself 23.0 points")
    send(dual, 5, "You inflicted 6.0 points of damage")
    send(dual, 6, "You received Shrapnel x (100) Value: 1.0 PED")
    points = project(dual)
    assert [p["healing_cost"] for p in points] == [0, 0]
    assert points.healing_pending == .02
    assert any("Incoming combat" in reason for reason in points.healing_pending_reasons)
    assert_conserved(dual, points)


def test_cast_state_and_expense_roll_back_together_on_failed_event_commit(dual):
    app, sid = dual
    send(dual, 0, MARKER)
    with app.store.connect() as conn:
        before = conn.execute("SELECT state FROM healing_inference_state WHERE session_id=?", (sid,)).fetchone()[0]
        conn.executescript("""CREATE TRIGGER synthetic_commit_failure BEFORE UPDATE OF payload ON events
            WHEN NEW.raw_message LIKE '%You healed yourself%'
            BEGIN SELECT RAISE(ABORT, 'synthetic commit failure'); END;""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic commit failure"):
        send(dual, 0, "You healed yourself 55.0 points")
    with app.store.connect() as conn:
        assert conn.execute("SELECT state FROM healing_inference_state WHERE session_id=?", (sid,)).fetchone()[0] == before
        conn.execute("DROP TRIGGER synthetic_commit_failure")
        conn.commit()
    assert app.store.get_session(sid).healing_cost == 0
    send(dual, 0, "You healed yourself 55.0 points")
    send(dual, 2, "You healed yourself 8.0 points")
    assert app.store.get_session(sid).healing_cost == .1
    assert app.store.get_session(sid).estimated_healing_uses == 1


def test_corrupt_inference_state_preserves_raw_observation_without_guessing_cost(dual):
    app, sid = dual
    with app.store.connect() as conn:
        conn.execute("INSERT INTO healing_inference_state(session_id,state) VALUES(?,?)", (sid, "{broken"))
        conn.commit()
    send(dual, 0, MARKER)
    send(dual, 0, "You healed yourself 55.0 points")
    assert app.store.get_session(sid).events == 2
    assert app.store.get_session(sid).healing_cost == 0
    assert all("healing_cost" not in row["payload"] for row in rows(dual))


def test_restoration3_published_quote_does_not_enable_automatic_price_even_without_override_file(dual, tmp_path):
    app, sid = dual
    record = app.catalog.healing_tools["Restoration Chip 3 (L)"]
    assert record.max_tt == 6.4
    assert record.cost_verified is False
    assert record.cost_per_use is None
    assert record.has_hot and record.hot_fraction is None and record.hot_duration is None
    app.store.set_selected_hot_tool(record.name)
    send(dual, 0, MARKER)
    send(dual, 0, "You healed yourself 4.5 points")
    app.store = Store(app.store.db_path)
    send(dual, 2, "You healed yourself 6.0 points")
    assert app.store.get_session(sid).healing_cost == 0
    # A frozen catalog snapshot stays fail-closed even if its companion note is absent.
    from dataclasses import asdict
    (tmp_path / "healing_tools.json").write_text(json.dumps({"items": [asdict(record)]}), encoding="utf-8")
    assert Catalog.load(tmp_path).healing_tools[record.name].cost_per_use is None


@pytest.mark.parametrize("text", [
    "You healed yourself 0.2 points", "You healed Synthetic Avatar with 0.2 points",
    "You healed yourself 29.0 points",
])
def test_capped_or_out_of_range_instant_receipts_are_not_fabricated_paid_uses(dual, text):
    app, sid = dual
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    send(dual, 0, text)
    summary = app.store.get_session(sid)
    assert (summary.healing_cost, summary.estimated_healing_uses, summary.unpriced_heal_events) == (0, 0, 1)


def test_same_hot_in_both_selectors_is_one_profile_and_one_charge(dual):
    app, sid = dual
    app.store.set_selected_healing_tool(HOT)
    send(dual, 0, MARKER)
    send(dual, 0, "You healed yourself 55.0 points")
    send(dual, 2, "You healed yourself 8.0 points")
    assert app.store.get_session(sid).healing_cost == .1
    assert app.store.get_session(sid).estimated_healing_uses == 1


@pytest.mark.parametrize("missing_timestamp", [False, True])
def test_external_evidence_unassigns_existing_expense_without_repricing_or_stale_fan(dual, missing_timestamp):
    app, sid = dual
    engine, recorder = HuntAttribution(app.store), TrailRecorder(app.store)
    project(dual, engine, recorder)
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, MARKER)
    send(dual, 1, "You healed yourself 55.0 points")
    send(dual, 2, "You received Shrapnel x (100) Value: 1.0 PED")
    paid = next(row for row in rows(dual) if "healing_cost" in row["payload"])
    before = project(dual, engine, recorder)
    anchor = before[0]["event_id"]
    history = app.store.load_chart_trails(sid, [anchor])[anchor]
    assert history[-1][0] == pytest.approx(BASE + .1)
    raw = ("[System] [] You were healed 30.0 points by Synthetic Avatar" if missing_timestamp else
           message(3, "You were healed 30.0 points by Synthetic Avatar"))
    app._process_lines([raw], sid, None)
    after = project(dual, engine, recorder)
    assert after[0]["cost"] == pytest.approx(BASE)
    assert after.healing_pending == .1
    assert app.store.load_chart_trails(sid, [anchor])[anchor][-1][0] == pytest.approx(BASE)
    assert app.store.load_chart_trails(sid, [anchor])[anchor][:len(history)] == history
    assert next(row for row in rows(dual) if row["id"] == paid["id"]) == paid
    assert any(row["raw_message"] == raw for row in rows(dual))
    assert_conserved(dual, after)


def test_shared_weapon_pool_never_absorbs_healing_and_uncertain_recipient_remains_pending(dual):
    app, sid = dual
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    engine = HuntAttribution(app.store)
    project(dual, engine)
    for index in range(5):
        at = index * 10
        send(dual, at, "You inflicted 6.0 points of damage")
        send(dual, at + 1, "You inflicted 6.0 points of damage")
        send(dual, at + 2, "You received Shrapnel x (100) Value: 1.0 PED")
        project(dual, engine)
    for at in range(60, 66):
        send(dual, at, "You inflicted 6.0 points of damage")
    send(dual, 66, "You healed yourself 23.0 points")
    for at in (67, 69, 71):
        send(dual, at, "You received Shrapnel x (100) Value: 1.0 PED")
        points = project(dual, engine)
    shared = [point for point in points if "shared" in point.get("cost_status", "")]
    assert len(shared) == 3
    assert [point["cost"] for point in shared] == pytest.approx([2 * BASE] * 3)
    assert all(point["cost_pool_total"] == pytest.approx(6 * BASE) for point in shared)
    assert all(point["healing_cost"] == 0 for point in shared)
    assert points.healing_pending == .02
    assert any("Shared/overlapping" in reason for reason in points.healing_pending_reasons)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(16 * BASE + .02)
    assert_conserved(dual, points)


def test_copied_run_exclude_restore_restart_repeats_and_new_ingestion_keep_healing_and_fans_consistent(dual, tmp_path):
    app, sid = dual
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    engine, recorder = HuntAttribution(app.store), TrailRecorder(app.store)
    project(dual, engine, recorder)
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, "You received Synthetic Strongbox Key x (1) Value: 500.0 PED")
    send(dual, 1, "You received Shrapnel x (5) Value: 0.0005 PED")
    send(dual, 3, "You healed yourself 23.0 points")
    source_points = project(dual, engine, recorder)
    anchor = source_points[0]["event_id"]
    receipt = next(row["id"] for row in rows(dual) if row["payload"].get("value") == 500)
    originals = rows(dual)
    original_store = app.store
    copy_path = tmp_path / "copied-run.sqlite3"
    with original_store.connect() as source, sqlite3.connect(copy_path) as target:
        source.backup(target)
    clone = object.__new__(type(app))
    clone.catalog, clone.store = app.catalog, Store(copy_path)
    run = clone, sid
    engine, recorder = HuntAttribution(clone.store), TrailRecorder(clone.store)
    assert clone.store.set_loot_excluded(sid, receipt, True)
    revision = clone.store.get_accounting_revision(sid)
    assert not clone.store.set_loot_excluded(sid, receipt, True)
    assert clone.store.get_accounting_revision(sid) == revision
    points = project(run, engine, recorder)
    assert points[0]["loot"] == pytest.approx(.0005)
    assert points[0]["cost"] == pytest.approx(BASE + .02)
    assert clone.store.load_chart_trails(sid, [anchor])[anchor][-1] == pytest.approx((BASE + .02, BASE + .02, .0005))
    assert rows(run) == originals
    assert_conserved(run, points)
    clone.store = Store(copy_path)
    engine, recorder = HuntAttribution(clone.store), TrailRecorder(clone.store)
    send(run, 6, "You inflicted 6.0 points of damage")
    send(run, 7, "You received Shrapnel x (100) Value: 1.0 PED")
    send(run, 8, "You healed yourself 23.0 points")
    assert_conserved(run, project(run, engine, recorder))
    assert clone.store.set_loot_excluded(sid, receipt, False)
    assert clone.store.get_session(sid).loot_value == pytest.approx(501.0005)
    assert_conserved(run, project(run, engine, recorder))
    assert clone.store.set_loot_excluded(sid, receipt, True)
    points = project(run, engine, recorder)
    assert clone.store.get_session(sid).loot_value == pytest.approx(1.0005)
    assert clone.store.get_session(sid).healing_cost == pytest.approx(.04)
    assert all(sample[2] == pytest.approx(.0005) for sample in clone.store.load_chart_trails(sid, [anchor])[anchor])
    assert rows(run)[:len(originals)] == originals
    with original_store.connect() as conn:
        original_payloads = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM events ORDER BY id")]
    assert original_payloads == [row["payload"] for row in originals]
    assert_conserved(run, points)


def test_legacy_gap_cannot_complete_pending_hot_activation_or_reprice_its_later_tick(dual):
    from dataclasses import asdict
    app, sid = dual
    send(dual, 0, MARKER)
    handoff = {"cost_setup": asdict(app.store.get_active_loadout())}
    app._process_lines([message(0, "You healed yourself 55.0 points")], sid, None, handoff=handoff)
    send(dual, 1, "You healed yourself 55.0 points")
    assert app.store.get_session(sid).healing_cost == 0
    assert app.store.get_session(sid).estimated_healing_uses == 0


def test_late_unpaid_receipt_unassigns_heal_even_without_prior_attribution_and_reopen_corrects_existing_fan(dual):
    app, sid = dual
    app.store.set_selected_hot_tool(None)
    app.store.set_selected_healing_tool(FAP)
    send(dual, 0, "You inflicted 6.0 points of damage")
    send(dual, 1, "You healed yourself 23.0 points")
    send(dual, 2, "You received Shrapnel x (100) Value: 1.0 PED")
    before = project(dual)
    anchor = before[0]["event_id"]
    TrailRecorder(app.store).update(sid, before, True)
    history = app.store.load_chart_trails(sid, [anchor])[anchor]
    assert history[-1][0] == pytest.approx(BASE + .02)
    paid = next(row for row in rows(dual) if "healing_cost" in row["payload"])
    # No HUD/attribution refresh while another return arrives without a shot.
    send(dual, 5, "You received Shrapnel x (25) Value: 0.25 PED")
    app.store = Store(app.store.db_path)
    after = project(dual, HuntAttribution(app.store), TrailRecorder(app.store))
    assert after.healing_pending == .02
    assert all(point["healing_cost"] == 0 for point in after)
    corrected = app.store.load_chart_trails(sid, [anchor])[anchor]
    assert corrected == history + ((BASE, BASE, 1.0),)
    assert next(row for row in rows(dual) if row["id"] == paid["id"]) == paid
    assert_conserved(dual, after)
