"""Selected healing inputs: real parser/ingestion/SQLite and source-unit boundaries."""
from dataclasses import asdict, replace
import importlib.util
import io
import json
from pathlib import Path

import pytest

from ped_hunter import cli
from ped_hunter.app import PedHunterApp, _kill_trend_points, streamer_metrics
from ped_hunter.catalog import Catalog, HealingToolRecord
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
    ingest(worker, line(1), line(4, "You healed Test Avatar with 0.2 points"))
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
    ingest(worker, line(1))
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
    assert (summary.events, summary.healing_cost, summary.estimated_healing_uses) == (0, 0, 0)


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
    ingest(worker, original, line(5))
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
    ingest(worker, line(7))
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


def test_heals_are_run_cost_not_phantom_creature_points_or_crafting_attempts(worker):
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    ingest(worker, line(0, "You inflicted 6.0 points of damage"),
           line(1, "You received Shrapnel x (100) Value: 0.01 PED"))
    before = _kill_trend_points(app.store, sid)
    ingest(worker, line(4), line(7, "You healed Test Avatar with 23.0 points"))
    after = _kill_trend_points(app.store, sid)
    assert after == before
    summary = app.store.get_session(sid)
    assert sum(point["cost"] for point in after) + summary.healing_cost == pytest.approx(summary.hunting_cost)
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
    return {"Name": name, "Properties": {"MaxHeal": 28.6, "UsesPerMinute": rate, "Economy": economy},
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
