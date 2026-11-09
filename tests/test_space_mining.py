"""Type M source, real import paths and observed space-mining accounting."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import queue

import pytest

from ped_hunter.app import PedHunterApp, _event_consumes_shot, _kill_trend_points, calculate_loadout_cost
from ped_hunter.catalog import Catalog
from ped_hunter.cli import _normalize
from ped_hunter.parser import parse_line
from ped_hunter.storage import LoadoutRecord, Store


NAME = "Basic Mining Laser, Type M (L)"


def test_exact_type_m_source_units_and_search_do_not_alias_type_a():
    catalog = Catalog.load()
    weapon = catalog.find_weapon("basic mining laser, type m")
    assert weapon.name == NAME
    assert (weapon.ammo, weapon.decay, weapon.cost_per_shot) == pytest.approx((212, 0.0011, 0.0223))
    assert (weapon.min_tt, weapon.max_tt) == (0, 10)
    assert weapon.category == "Mining Laser (Low)"
    assert catalog.resolve_weapon_name("Basic Mining Laser, Type A (L)") != NAME
    assert calculate_loadout_cost(catalog=catalog, weapon_name=NAME) == pytest.approx((212, 0.0011))
    root = Path(__file__).resolve().parents[1] / "data/catalog"
    source = json.loads((root / "weapon_supplements.json").read_text())["items"][0]
    assert source["source_record"]["Id"] == 3058
    assert source["source_record"]["ItemId"] == 2003058
    assert source["source_record"]["Properties"]["Economy"]["Decay"] == pytest.approx(0.11)
    assert source["ammo_source_record"]["Properties"]["Economy"]["MaxTT"] == pytest.approx(0.0001)
    assert source["source_record"]["AttachmentType"]["Name"] is None


@pytest.mark.parametrize("modifier", [
    {"amp": "Omegaton A101"}, {"scope": "Alekz Precision Scope"},
    {"sight_1": "Alekz Precision Laser Sight"}, {"sight_2": "unknown"},
    {"damage_enhancers": 1}, {"economy_enhancers": 1},
])
def test_no_unverified_mining_laser_attachment_or_enhancer_costs(modifier):
    with pytest.raises(ValueError, match="unverified"):
        calculate_loadout_cost(catalog=Catalog.load(), weapon_name=NAME, **modifier)


@pytest.mark.parametrize("method", ["cli", "tool"])
def test_real_catalog_refresh_keeps_exact_type_m_once_and_preserves_type_a(tmp_path, monkeypatch, method):
    legacy = {
        "weapons.json": {"data": {
            " basic mining laser, type m (l) ": {"type": "Ranged", "ammo": 9999, "decay": 0.11},
            "Basic Mining Laser, Type A (L)": {"type": "Mining Laser (Low)", "ammo": 400, "decay": 0.01},
        }},
        **{name: {"data": {}} for name in ("attachments.json", "scopes.json", "sights.json", "resources.json", "crafting.json")},
    }
    if method == "cli":
        rows = _normalize(legacy)["weapons.json"]["items"]
    else:
        path = Path(__file__).resolve().parents[1] / "tools/sync_legacy_data.py"
        spec = importlib.util.spec_from_file_location("space_mining_sync", path)
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        monkeypatch.setattr(sync, "__file__", str(tmp_path / "tools/sync_legacy_data.py"))
        monkeypatch.setattr(sync, "download", lambda name: legacy[name])
        assert sync.main() == 0
        rows = json.loads((tmp_path / "data/catalog/weapons.json").read_text())["items"]
    matches = [item for item in rows if item["name"].strip().casefold() == NAME.casefold()]
    assert len(matches) == 1
    target = tmp_path / "loaded"
    target.mkdir()
    (target / "weapons.json").write_text(json.dumps({"items": rows}))
    catalog = Catalog.load(target)
    assert catalog.find_weapon(NAME).cost_per_shot == pytest.approx(0.0223)
    assert catalog.find_weapon("Basic Mining Laser, Type A (L)").cost_per_shot == pytest.approx(0.05)
    assert matches[0]["source_url"] == "https://api.entropianexus.com/weapons/3058"


@pytest.mark.parametrize("message,consumes", [
    ("You inflicted 0.0 points of damage", True),
    ("You inflicted 4.6 points of damage", True),
    ("Critical hit - Additional damage! You inflicted 17.4 points of damage", True),
    ("You missed", True), ("You missed.", True),
    ("The attack missed you", False), ("You took 10.0 points of damage", False),
    ("You Evaded the attack", False), ("You healed yourself 23.0 points", False),
    ("Your Basic Mining Laser, Type M (L) has reached tier 0.41", False),
    ("You have gained 0.3734 experience in your Mining Laser Technology skill", False),
])
def test_observed_messages_count_uses_not_damage_or_other_activity(message, consumes):
    event = parse_line("2026-11-09 02:21:00 [System] [] " + message)
    assert bool(event and _event_consumes_shot(event)) is consumes


def mining_worker(tmp_path):
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / "space.sqlite3")
    app.catalog = Catalog.load()
    app._log_queue = queue.Queue()
    ammo, decay = calculate_loadout_cost(catalog=app.catalog, weapon_name=NAME)
    loadout = LoadoutRecord(id=None, name="Type M unmodified", weapon=NAME,
                           ammo_burn=ammo, decay=decay, cost_per_shot=decay + ammo / 10000)
    loadout.id = app.store.save_loadout(loadout, make_active=True)
    return app, loadout


def test_real_ingestion_prices_misses_once_preserves_history_and_uses_reported_loot_value(tmp_path):
    app, loadout = mining_worker(tmp_path)
    old = app.store.start_session("hunt", loadout)
    app.store.add_event(old, {"kind": "loot", "raw_message": "old unknown loot", "payload": {"value": 0.4, "quantity": 7, "item_name": "Old item"}})
    app.store.end_session(old)
    before = asdict(app.store.get_session(old))
    with app.store.connect() as conn:
        old_rows = [tuple(row) for row in conn.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (old,))]
    sid = app.store.start_session("hunt", loadout)
    hit = "2026-11-09 02:20:33 [System] [] You inflicted 4.6 points of damage"
    miss = "2026-11-09 02:20:48 [System] [] You missed"
    lines = [hit, hit, miss,
             "2026-11-09 02:20:49 [System] [] The attack missed you",
             "2026-11-09 02:20:50 [System] [] You have gained 0.3734 experience in your Mining Laser Technology skill",
             "2026-11-09 02:20:58 [System] [] Your Basic Mining Laser, Type M (L) has reached tier 0.38",
             "2026-11-09 02:21:31 [System] [] Critical hit - Additional damage! You inflicted 17.4 points of damage",
             "2026-11-09 02:22:01 [System] [] You received Caldorite Stone x (4) Value: 0.68 PED",
             "2026-11-09 02:22:01 [System] [] You received Shrapnel x (1666) Value: 0.1666 PED",
             "2026-11-09 02:22:01 [System] [] You received Iron Stone x (1) Value: 0.13 PED",
             "2026-11-09 02:22:01 [System] [] You received Tailings x (318) Value: 0.0318 PED"]
    _, previous = app._process_lines(lines, sid, None)
    summary = app.store.get_session(sid)
    assert summary.hunting_cost == pytest.approx(3 * 0.0223)
    assert summary.repair_shots == 3
    assert summary.repair_decay == pytest.approx(3 * 0.0011)
    assert summary.loot_value == pytest.approx(1.0084)  # Total explicit values, NOT multiplied by quantities again.
    assert summary.combat_damage == pytest.approx(22.0)
    app._process_lines([lines[-1]], sid, previous)  # Replay at next poll boundary.
    assert app.store.get_session(sid).loot_value == pytest.approx(1.0084)
    app._process_lines(["2026-11-09 02:23:01 [System] [] You inflicted 4.6 points of damage",
                        "2026-11-09 02:23:02 [System] [] You missed"], sid, previous)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(5 * 0.0223)
    points = _kill_trend_points(app.store, sid)
    assert sum(p["cost"] for p in points) == pytest.approx(5 * 0.0223)
    assert sum(p["loot"] for p in points) == pytest.approx(1.0084)
    assert asdict(app.store.get_session(old)) == before
    with app.store.connect() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM events WHERE session_id=? ORDER BY id", (old,))] == old_rows


def test_missing_setup_does_not_invent_tool_or_use_price(tmp_path):
    app = object.__new__(PedHunterApp)
    app.store = Store(tmp_path / "no-setup.sqlite3")
    app.catalog = Catalog.load()
    sid = app.store.start_session("hunt")
    app._process_lines(["2026-11-09 02:21:18 [System] [] You missed"], sid, None)
    assert app.store.get_session(sid).hunting_cost == 0
    with app.store.connect() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM events WHERE session_id=?", (sid,)).fetchone()[0])
    assert payload == {"outgoing_miss": True}  # Explicit direction, not a legacy ambiguous miss or zero-priced equipment.
