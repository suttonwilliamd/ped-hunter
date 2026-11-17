"""Selected healing inputs: real parser/ingestion/SQLite and source-unit boundaries."""
from dataclasses import asdict, replace
import importlib.util
import io
import json
from pathlib import Path
import queue
import sqlite3

import pytest

from ped_hunter import cli
from ped_hunter.app import PedHunterApp, _kill_trend_points, _load_resume_view, streamer_metrics
from ped_hunter.catalog import Catalog, HealingToolRecord
from ped_hunter.chart_trails import TrailRecorder
from ped_hunter.healing import NEXUS_API, normalize_healing_catalog
from ped_hunter.parser import parse_line
from ped_hunter.storage import LoadoutRecord, Store

FAP = "Frontier First Aid Pack, Adjusted"
CHIP = "Regeneration Chip 8"


def line(second, text="You healed yourself 23.0 points"):
    return f"2026-11-08 10:00:{second:02d} [System] [] {text}"


@pytest.fixture
def worker(tmp_path):
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / "healing.sqlite3")
    app.catalog = Catalog.load()
    gear = LoadoutRecord(None, "Synthetic blade", "SET Combat Blade (L)",
                         ammo_burn=65, decay=0.00001, cost_per_shot=0.00651)
    app.store.save_loadout(gear, make_active=True)
    return app, app.store.start_session("hunt", gear)


def ingest(worker, *lines):
    app, sid = worker
    return app._process_lines(list(lines), sid, None)


def rows(worker):
    app, sid = worker
    with app.store.connect() as conn:
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in conn.execute(
            "SELECT id,timestamp,kind,raw_message,payload FROM events WHERE session_id=? ORDER BY id", (sid,))]


def test_self_and_other_heals_add_cost_without_shots_or_weapon_repair_decay(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(0, "You inflicted 6.0 points of damage"))
    weapon = asdict(app.store.get_active_loadout())
    ingest(worker, line(1), line(4, "You healed Test Avatar with 23.0 points"))
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == pytest.approx(0.00651 + 0.02 + 0.02)
    assert summary.healing_cost == pytest.approx(0.04)
    assert summary.estimated_healing_uses == 2
    assert summary.unpriced_heal_events == 0
    assert (summary.combat_damage, summary.repair_shots) == (6, 1)
    assert asdict(app.store.get_active_loadout()) == weapon
    for row in rows(worker)[1:]:
        payload = row["payload"]
        assert payload["healing_tool"] == FAP
        assert payload["healing_cost_estimated"] is True
        assert "shot_cost" not in payload and "repair_decay" not in payload
    assert [r["payload"]["healing_target"] for r in rows(worker)[1:]] == ["self", "Test Avatar"]
    assert streamer_metrics(summary)["cost"] == pytest.approx(summary.hunting_cost)
    totals = app.store.lifetime_totals()
    assert totals.total_cost == pytest.approx(summary.hunting_cost)
    assert totals.total_net == pytest.approx(-summary.hunting_cost)


def test_chip_counts_actual_essence_units_and_pec_decay_in_ped(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(CHIP)
    ingest(worker, line(1, "You healed yourself 55.0 points"))
    payload = rows(worker)[0]["payload"]
    assert payload["healing_decay_cost"] == pytest.approx(2.8 / 100)
    assert payload["healing_ammo_cost"] == pytest.approx(420 * 0.0001)
    assert payload["healing_cost"] == pytest.approx(0.07)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(0.07)
    assert app.store.get_session(sid).repair_shots == 0


def test_selection_changes_clear_and_missing_catalog_leave_unpriced_not_free_uses(worker):
    app, sid = worker
    ingest(worker, line(1))
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(4))
    app.store.set_selected_healing_tool(None)
    ingest(worker, line(7))
    app.store.set_selected_healing_tool("Unavailable future tool")
    ingest(worker, line(10))
    summary = app.store.get_session(sid)
    assert (summary.estimated_healing_uses, summary.unpriced_heal_events) == (1, 3)
    assert summary.hunting_cost == pytest.approx(0.02)
    assert [r["payload"].get("healing_cost") for r in rows(worker)] == [None, 0.02, None, None]
    reopened = Store(app.store.db_path)
    assert reopened.get_selected_healing_tool() == "Unavailable future tool"
    reopened.set_selected_healing_tool(None)
    assert Store(app.store.db_path).get_selected_healing_tool() is None


@pytest.mark.parametrize("record", [
    HealingToolRecord("Unknown decay", None),
    HealingToolRecord("Unknown burn", 0.02, ammo=None),
    HealingToolRecord("Unknown unit", 0.02, ammo=420, ammo_name="Unknown essence"),
])
def test_missing_source_cost_never_becomes_a_zero_cost_use(worker, record):
    app, sid = worker
    app.catalog.healing_tools[record.name] = record
    app.store.set_selected_healing_tool(record.name)
    ingest(worker, line(1))
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == 0
    assert (summary.estimated_healing_uses, summary.unpriced_heal_events) == (0, 1)
    assert "healing_cost" not in rows(worker)[0]["payload"]


@pytest.mark.parametrize("name", ["EMT Kit Ek-2600, Modified", "Restoration Chip, Modified", "Regeneration Field Chip 8"])
def test_periodic_and_area_tool_messages_are_not_counted_as_activations(worker, name):
    app, sid = worker
    assert app.catalog.healing_tools[name].tracking_limit
    app.store.set_selected_healing_tool(name)
    ingest(worker, line(1), line(2), line(3, "You healed Test Avatar with 23.0 points"))
    summary = app.store.get_session(sid)
    assert (summary.healing_cost, summary.estimated_healing_uses, summary.unpriced_heal_events) == (0, 0, 3)
    assert all("healing_cost" not in row["payload"] for row in rows(worker))


@pytest.mark.parametrize("message", [
    "Another Avatar healed you 23.0 points",
    "You have been healed 23.0 points",
    "You are not wounded", "That person isn't wounded", "Healing is diminished while moving",
    "You healed yourself 23.0 points (Heal Over Time)",
    "You regenerated 23.0 health points", "You healed yourself 23..0 points",
])
def test_unrelated_failures_received_and_unrecognized_periodic_forms_are_not_uses(worker, message):
    # Explicit periodic suffixes are defensive examples, not claimed real log grammar.
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(1, message))
    summary = app.store.get_session(sid)
    assert (summary.events, summary.healing_cost, summary.estimated_healing_uses) == (
        1 if message == "Healing is diminished while moving" else 0, 0, 0)


def test_chat_quoting_system_healing_is_not_personal_healing(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, "2026-11-08 10:00:01 [Rookie] [Example Avatar] [System] [] You healed yourself 23.0 points")
    summary = app.store.get_session(sid)
    assert summary.events == 1
    assert (summary.healing_cost, summary.estimated_healing_uses) == (0, 0)
    assert rows(worker)[0]["kind"] == "chat"


def test_duplicate_replay_interleaved_after_store_reopen_and_old_tail_is_not_recharged(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    original = line(1)
    ingest(worker, original)
    # More than a recent-window cache; the retained-history identity index must work.
    with app.store.connect() as conn:
        conn.executemany("INSERT INTO events(session_id,timestamp,kind,raw_message,payload) VALUES(?,NULL,'skill',?,?)",
                         [(sid, f"synthetic skill {n}", '{"skill":"First Aid","xp":0.01}') for n in range(300)])
        conn.commit()
    app.store = Store(app.store.db_path)
    app.store.set_selected_healing_tool("Vivo T1")
    ingest(worker, original, line(5, "You healed yourself 9.0 points"))
    summary = app.store.get_session(sid)
    assert summary.healing_cost == pytest.approx(0.02 + 0.00903)
    assert (summary.estimated_healing_uses, summary.unpriced_heal_events) == (2, 0)
    saved = rows(worker)
    assert saved[-2]["raw_message"] == saved[0]["raw_message"]
    assert saved[-2]["payload"]["healing_duplicate_of_event_id"] == saved[0]["id"]
    assert "healing_cost" not in saved[-2]["payload"]
    assert saved[0]["payload"]["healing_cost"] == pytest.approx(0.02)


def test_distinct_timestamp_equal_amount_heals_are_separate_and_missing_timestamp_is_unpriced(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(1), line(4), line(7), "[System] [] You healed yourself 23.0 points")
    summary = app.store.get_session(sid)
    assert summary.healing_cost == pytest.approx(0.06)
    assert (summary.estimated_healing_uses, summary.unpriced_heal_events) == (3, 1)


def test_selection_persists_across_runs_weapon_changes_resume_and_new_store_without_repricing(worker):
    app, original = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(1))
    first = rows(worker)[0]
    app.store.end_session(original)
    rifle = LoadoutRecord(None, "Second setup", "Frontier Rifle", ammo_burn=100, decay=0.0002, cost_per_shot=0.0102)
    app.store.save_loadout(rifle, make_active=True)
    later = app.store.start_session("hunt", rifle)
    ingest((app, later), line(4))
    assert app.store.get_selected_healing_tool() == FAP
    app.store.set_selected_healing_tool("Vivo T1")
    app.store.end_session(later)
    app.store = Store(app.store.db_path)
    assert app.store.get_selected_healing_tool() == "Vivo T1"
    app.store.resume_session(original)
    ingest(worker, line(7, "You healed yourself 9.0 points"))
    assert app.store.get_session(original).healing_cost == pytest.approx(0.02903)
    assert app.store.get_session(later).healing_cost == pytest.approx(0.02)
    assert rows(worker)[0] == first
    # A new catalog quote does not modify old cost snapshots or session totals.
    app.catalog.healing_tools[FAP] = replace(app.catalog.healing_tools[FAP], decay=3.0)
    app.store.set_selected_healing_tool(FAP)
    assert app.store.get_session(original).healing_cost == pytest.approx(0.02903)
    assert rows(worker)[0] == first


def test_legacy_heals_keep_original_payload_and_are_not_retroactively_priced_even_on_replay(worker):
    app, sid = worker
    event = parse_line(line(1)).to_row()
    event["payload"] = {"healed": 23.0}
    app.store.add_event(sid, event)
    original = rows(worker)[0]
    app.store.set_selected_healing_tool(FAP)
    app.store = Store(app.store.db_path)
    ingest(worker, line(1))
    summary = app.store.get_session(sid)
    assert (summary.healing_cost, summary.estimated_healing_uses, summary.unpriced_heal_events) == (0, 0, 0)
    assert rows(worker)[0] == original
    assert "healing_cost" not in rows(worker)[1]["payload"]


def test_future_heals_use_previous_loot_without_phantom_groups_or_backfilling_other_targets(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(0, "You inflicted 6.0 points of damage"),
           line(1, "You received Shrapnel x (100) Value: 0.01 PED"))
    before = _kill_trend_points(app.store, sid)
    ingest(worker, line(4), line(7, "You healed Test Avatar with 23.0 points"))
    after = _kill_trend_points(app.store, sid)
    assert len(after) == len(before) == 1
    assert before[0]["cost"] == pytest.approx(0.00651)
    assert after[0]["weapon_cost"] == pytest.approx(0.00651)
    assert after[0]["cost"] == pytest.approx(0.00651 + 0.02)
    assert after.healing_pending == pytest.approx(0.02)
    summary = app.store.get_session(sid)
    assert sum(point["cost"] for point in after) + after.healing_pending == pytest.approx(summary.hunting_cost)
    assert summary.manufacturing_attempt == 0
    assert summary.loot_value == pytest.approx(0.01)
    assert summary.net_value == pytest.approx(0.01 - 0.00651 - 0.04)


def test_ended_session_does_not_receive_healing_costs(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    app.store.end_session(sid)
    ingest(worker, line(1))
    assert app.store.get_session(sid).events == 0
    assert app.store.get_selected_healing_tool() == FAP


def test_cli_monitor_uses_same_global_healing_selection_and_duplicate_accounting(tmp_path):
    path = tmp_path / "chat.log"
    path.write_text("\n".join([line(1), line(2, "Your First Aid has improved by 0.25"), line(1), line(5)]) + "\n")
    store = Store(tmp_path / "cli.sqlite3")
    store.set_selected_healing_tool(FAP)
    assert cli._monitor(path, store.db_path, "hunt", once=True) == 0
    session = store.list_recent_sessions(1)[0]
    assert session.healing_cost == pytest.approx(0.04)
    assert session.hunting_cost == pytest.approx(0.04)
    assert session.estimated_healing_uses == 2
    assert session.ended_at is not None


def medical_source(name, decay, *, burn=None, ammo=None, effects=(), rate=24, chip=False):
    economy = {"Decay": decay}
    if chip:
        economy["AmmoBurn"] = burn
    return {"Name": name, "Properties": {"MinHeal": 21.4, "MaxHeal": 28.6, "UsesPerMinute": rate, "Economy": economy},
            "EffectsOnUse": [{"Name": name} for name in effects], "EffectsOnEquip": [],
            "Ammo": {"Name": ammo} if ammo else None,
            "Links": {"$Url": "/medicalchips/25" if chip else "/medicaltools/253"}}


@pytest.mark.parametrize("importer", ["cli", "legacy_sync"])
def test_real_catalog_refresh_paths_keep_separate_sourced_healers_with_correct_costs(tmp_path, monkeypatch, importer, worker):
    consumptive = medical_source("Synthetic consumptive medical tool", 2, ammo="Mind Essence")
    consumptive["Properties"]["Economy"]["AmmoBurn"] = 65
    tools = [medical_source(FAP, 2), medical_source("Unknown source FAP", None), consumptive]
    chips = [medical_source(CHIP, 2.8, chip=True, burn=420, ammo="Mind Essence")]
    material = {"Name": "Mind Essence", "Properties": {"Economy": {"MaxTT": 0.0001}}}
    other_material = {"Name": "Light Mind Essence", "Properties": {"Economy": {"MaxTT": 0.0001}}}
    feeds = dict(zip(("/medicaltools", "/medicalchips", "/materials/1447", "/materials/1288"), (tools, chips, material, other_material)))
    monkeypatch.setattr("ped_hunter.healing.urllib.request.urlopen", lambda url, **_kw: io.BytesIO(json.dumps(feeds[url.removeprefix(NEXUS_API)]).encode()))
    if importer == "cli":
        monkeypatch.setattr(cli, "__file__", str(tmp_path / "src/ped_hunter/cli.py"))
        monkeypatch.setattr(cli, "_download_json", lambda _name: {"data": {}})
        assert cli._seed_data(force=True) == 0
    else:
        spec = importlib.util.spec_from_file_location("healing_sync", Path(__file__).resolve().parents[1] / "tools/sync_legacy_data.py")
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        monkeypatch.setattr(sync, "__file__", str(tmp_path / "tools/sync_legacy_data.py"))
        monkeypatch.setattr(sync, "download", lambda _name: {"data": {}})
        assert sync.main() == 0
    catalog = Catalog.load(tmp_path / "data/catalog")
    assert catalog.healing_tools[FAP].cost_per_use == pytest.approx(0.02)
    assert catalog.healing_tools[CHIP].cost_per_use == pytest.approx(0.07)
    assert catalog.healing_tools["Unknown source FAP"].cost_per_use is None
    assert FAP not in catalog.weapons and CHIP not in catalog.weapons
    tool = catalog.healing_tools["Synthetic consumptive medical tool"]
    assert tool.cost_per_use == pytest.approx(0.0265)
    app, sid = worker
    app.catalog.healing_tools[tool.name] = tool
    app.store.set_selected_healing_tool(tool.name)
    ingest(worker, line(1))
    assert app.store.get_session(sid).healing_cost == pytest.approx(0.0265)
    assert rows(worker)[0]["payload"]["healing_ammo_cost"] == pytest.approx(0.0065)


@pytest.mark.parametrize("kwargs", [dict(effects=("Heal Over Time",)), dict(effects=("Regeneration Per Second",)), dict(effects=("Lifesteal Added",)), dict(rate=120)])
def test_import_metadata_blocks_periodic_or_indistinguishable_subsecond_activations(kwargs):
    imported = normalize_healing_catalog([medical_source("Synthetic medical tool", 2, **kwargs)], [], [])
    assert imported["items"][0]["tracking_limit"]


def test_schema_upgrade_preserves_legacy_malformed_payloads_and_existing_heal_rows(worker):
    app, sid = worker
    with app.store.connect() as conn:
        conn.execute("DROP INDEX idx_events_healing_identity")
        conn.execute("INSERT INTO events(session_id,kind,raw_message,payload) VALUES(?,'combat','old malformed','{broken')", (sid,))
        conn.commit()
    app.store = Store(app.store.db_path)
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(1))
    assert app.store.get_session(sid).healing_cost == pytest.approx(0.02)
    with app.store.connect() as conn:
        assert conn.execute("SELECT payload FROM events WHERE raw_message='old malformed'").fetchone()[0] == "{broken"


def test_real_file_ingestion_prices_only_subsequent_selected_heals(worker, tmp_path):
    from test_log_ingestion import ScriptedStop, append

    app, sid = worker
    app._log_queue = queue.Queue()
    path = tmp_path / "synthetic-chat.log"
    path.write_bytes(b"")
    app.store.set_selected_healing_tool(FAP)

    def use_chip():
        app.store.set_selected_healing_tool(CHIP)
        append(path, (line(4, "You healed yourself 55.0 points") + "\r\n").encode())

    def clear():
        app.store.set_selected_healing_tool(None)
        append(path, (line(7) + "\r\n").encode())

    app._log_worker_loop(path, sid, 0, ScriptedStop([
        lambda: append(path, (line(0, "You inflicted 6.0 points of damage") + "\r\n" + line(1) + "\r\n").encode()),
        use_chip, clear,
    ]))
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == pytest.approx(0.00651 + 0.02 + 0.07)
    assert (summary.estimated_healing_uses, summary.unpriced_heal_events) == (2, 1)
    assert [row["payload"].get("healing_cost") for row in rows(worker)] == [None, 0.02, 0.07, None]
    assert not any(kind == "error" for kind, _ in app._log_queue.queue)


def test_legacy_cutover_gap_cannot_inherit_new_healing_selection(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    handoff = {"cost_setup": asdict(app.store.get_active_loadout())}
    app._process_lines([line(1)], sid, None, handoff=handoff)
    ingest(worker, line(4))
    summary = app.store.get_session(sid)
    assert (summary.estimated_healing_uses, summary.unpriced_heal_events) == (1, 1)
    assert summary.healing_cost == pytest.approx(0.02)
    assert "healing_cost" not in rows(worker)[0]["payload"]


def test_corrected_synthetic_copy_reopens_without_stale_totals_or_fans_and_keeps_legacy_heals_unassigned(worker, tmp_path):
    """Frozen manual proof uses legacy expenses; future heals use new rules."""
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(0, "You inflicted 6.0 points of damage"),
           line(1, "You received Strongbox Key x (1) Value: 500 PED"),
           line(1, "You received Shrapnel x (5) Value: 0.0005 PED"))
    legacy = parse_line(line(4)).to_row()
    legacy["payload"].update({
        "healing_tool": FAP, "healing_cost": .02, "healing_decay_cost": .02,
        "healing_ammo_cost": 0.0, "healing_cost_estimated": True,
        "healing_cost_status": "selected_tool_estimate",
    })
    app.store.add_event(sid, legacy)
    points = _kill_trend_points(app.store, sid)
    assert len(points) == 1
    point = points[0]
    app.store.save_chart_trails(sid, {point["event_id"]: [(point["cost"], point["avg_cost"], point["loot"])]})
    original_rows = rows(worker)
    original_summary = asdict(app.store.get_session(sid))
    original_trails = app.store.load_chart_trails(sid, [point["event_id"]])
    outlier_id = next(row["id"] for row in original_rows if row["payload"].get("value") == 500)
    path = tmp_path / "corrected-synthetic-copy.sqlite3"
    with app.store.connect() as source, sqlite3.connect(path) as target:
        source.backup(target)
    copied = Store(path)
    with copied.connect() as conn:
        conn.execute("DELETE FROM events WHERE session_id=? AND id=?", (sid, outlier_id))
        conn.commit()
    # A deliberate fixture correction is required: event deletion alone is NOT
    # a general history/fan reconciliation feature in the current application.
    copied.save_chart_trails(sid, {point["event_id"]: [(point["cost"], point["avg_cost"], 0.0005)]})
    reopened = Store(path)
    view = _load_resume_view(reopened, sid)
    assert view.session.loot_value == pytest.approx(0.0005)
    assert view.session.hunting_cost == pytest.approx(0.00651 + 0.02)
    assert view.session.net_value == pytest.approx(0.0005 - 0.00651 - 0.02)
    assert view.totals.total_loot == pytest.approx(0.0005)
    assert view.totals.total_net == pytest.approx(view.session.net_value)
    assert view.sessions[0] == view.session
    assert len(view.points) == 1
    assert (view.points[0]["cost"], view.points[0]["avg_cost"], view.points[0]["loot"]) == pytest.approx((0.00651, 0.00651, 0.0005))
    assert view.points.healing_pending == pytest.approx(.02)
    histories = TrailRecorder(reopened).update(sid, view.points, live=False)
    assert histories == {point["event_id"]: ((point["cost"], point["avg_cost"], 0.0005),)}
    copy_app = object.__new__(PedHunterApp)
    copy_app.store, copy_app.catalog = reopened, app.catalog
    reopened.set_selected_healing_tool(CHIP)
    ingest((copy_app, sid), line(7, "You healed yourself 55.0 points"))
    after = _load_resume_view(reopened, sid)
    assert after.session.healing_cost == pytest.approx(0.09)
    assert after.totals.total_cost == pytest.approx(0.00651 + 0.09)
    assert after.session.net_value == pytest.approx(0.0005 - 0.00651 - 0.09)
    assert len(after.points) == 1
    assert after.points[0]["weapon_cost"] == pytest.approx(.00651)
    assert after.points[0]["healing_cost"] == pytest.approx(.07)
    assert after.points[0]["cost"] == pytest.approx(.00651 + .07)
    assert after.points.healing_pending == pytest.approx(.02)
    assert after.points[0]["cost"] + after.points.healing_pending == pytest.approx(after.session.hunting_cost)
    assert TrailRecorder(reopened).update(sid, after.points, live=False) == histories
    observed = TrailRecorder(reopened).update(sid, after.points, live=True)[point["event_id"]]
    assert observed[:-1] == histories[point["event_id"]]
    assert observed[-1] == pytest.approx((.00651 + .07, .00651 + .07, .0005))
    assert rows(worker) == original_rows
    assert asdict(app.store.get_session(sid)) == original_summary
    assert app.store.load_chart_trails(sid, [point["event_id"]]) == original_trails


@pytest.mark.parametrize("text", ["You healed yourself 0 points", "You healed Test Avatar with 0 points"])
def test_zero_healing_is_not_a_billable_activation(worker, text):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(1, text))
    summary = app.store.get_session(sid)
    assert (summary.healing_cost, summary.estimated_healing_uses) == (0, 0)
    assert "healing_cost" not in rows(worker)[0]["payload"]



VIVO = "Vivo S10"
MARKER = "Received Effect Over Time: Heal"
# Two real Vivo S10 clusters from the chat log: the marker only starts an effect; refresh casts
# (14.6, 14.9, 13.6) have no marker; small lines are ticks of the stacked effect.
VIVO_LOG = [
    (0, MARKER), (0, "You healed yourself 13.4 points"), (1, "You healed yourself 0.5 points"),
    (2, "You healed yourself 14.6 points"), (3, "You healed yourself 1.1 points"),
    (5, "You healed yourself 1.1 points"), (7, "You healed yourself 1.1 points"),
    (8, "You healed yourself 25.0 points"),  # Frontier FAP when selected, otherwise out of range.
    (9, "You healed yourself 1.1 points"), (12, "You healed yourself 0.1 points"),
    (40, MARKER), (40, "You healed yourself 12.6 points"), (41, "You healed yourself 0.3 points"),
    (42, "You healed yourself 14.9 points"), (43, "You healed yourself 1.1 points"),
    (44, "You healed Test Avatar with 13.6 points"), (45, "You healed yourself 1.5 points"),
    (47, "You healed yourself 1.5 points"), (49, "You healed yourself 1.5 points"),
]


@pytest.mark.parametrize("heal, hot, fap_casts", [(FAP, VIVO, 1), (VIVO, VIVO, 0), (VIVO, None, 0), (None, VIVO, 0)])
def test_vivo_s10_charges_once_per_activation_never_per_tick_or_per_list(worker, heal, hot, fap_casts):
    app, sid = worker
    # An earlier unknown effect (marker with no HoT selected) must not leave later Vivo uses free.
    ingest(worker, line(0, MARKER), line(1, "You healed yourself 13.0 points"))
    app.store.set_selected_healing_tool(heal)
    app.store.set_selected_hot_tool(hot)
    before = app.store.get_session(sid)
    ingest(worker, *(f"2026-11-08 10:{1 + s // 60:02d}:{s % 60:02d} [System] [] {text}" for s, text in VIVO_LOG))
    summary = app.store.get_session(sid)
    vivo_casts = 5
    expected = vivo_casts * 0.0133 + fap_casts * 0.02
    assert summary.healing_cost - before.healing_cost == pytest.approx(expected)
    assert summary.hunting_cost - before.hunting_cost == pytest.approx(expected)
    assert summary.estimated_healing_uses - before.estimated_healing_uses == vivo_casts + fap_casts
    new = [r["payload"] for r in rows(worker) if r["timestamp"] >= "2026-11-08T10:01:00"]
    booked = [p for p in new if p.get("healing_cost")]
    assert [p["healed"] for p in booked if p["healing_tool"] == VIVO] == [13.4, 14.6, 12.6, 14.9, 13.6]
    ticks = [p for p in new if p.get("healing_action") == "self" and p["healed"] < 2]
    assert ticks and all(p["healing_cost_status"] == "hot_tick" and "healing_cost" not in p for p in ticks)
    assert all("healing_cost" not in p for p in new if p.get("healing_action") == "effect_start")
    other = next(p for p in booked if p["healing_target"] == "Test Avatar")
    assert other["healing_allocation_eligible"] is False
    unpriced = summary.unpriced_heal_events - before.unpriced_heal_events
    assert unpriced == (1 - fap_casts)  # Without FAP selected the 25.0 line is left unpriced, not free.
