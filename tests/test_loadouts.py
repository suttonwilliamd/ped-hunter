from pathlib import Path

from ped_hunter.app import _event_consumes_shot, calculate_loadout_cost
from ped_hunter.catalog import Catalog
from ped_hunter.parser import ParsedEvent
from ped_hunter.storage import LoadoutRecord, Store


def test_frontier_loadout_cost_matches_weapon_cost():
    catalog = Catalog.load()
    ammo, decay = calculate_loadout_cost(catalog=catalog, weapon_name="Frontier Rifle")
    assert ammo == 100
    assert decay == 0.0002
    assert decay + (ammo / 10_000.0) == 0.0102


def test_damage_and_economy_enhancers_follow_lootnanny_formula():
    catalog = Catalog.load()
    ammo, decay = calculate_loadout_cost(
        catalog=catalog,
        weapon_name="Frontier Rifle",
        damage_enhancers=2,
        economy_enhancers=5,
    )
    assert ammo == 113
    assert round(decay, 6) == 0.000228


def test_store_summarizes_loadout_shot_costs(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    loadout = LoadoutRecord(
        id=None,
        name="Test Frontier",
        weapon="Frontier Hunting Rifle",
        ammo_burn=100,
        decay=0.0002,
        cost_per_shot=0.0102,
    )
    loadout.id = store.save_loadout(loadout, make_active=True)
    session_id = store.start_session("hunt", loadout)
    store.add_event(session_id, {"kind": "combat", "raw_message": "hit", "payload": {"damage": 6, "shot_cost": 0.0102}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "loot", "payload": {"value": 0.25}})

    summary = store.get_current_session()
    assert summary is not None
    assert summary.loadout_name == "Test Frontier"
    assert summary.hunting_cost == 0.0102
    assert summary.net_value == 0.2398


def test_session_summary_tolerates_malformed_legacy_payload(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)",
            (session_id, None, "loot", "legacy bad row", "not json"),
        )
        conn.commit()

    summary = store.get_current_session()
    assert summary is not None
    assert summary.events == 1
    assert summary.loot_value == 0


def test_only_outgoing_combat_consumes_shots():
    assert _event_consumes_shot(ParsedEvent("combat", None, "", {"damage": 4}))
    assert _event_consumes_shot(ParsedEvent("combat", None, "", {"dodged": True}))
    assert not _event_consumes_shot(ParsedEvent("combat", None, "", {"damage_taken": 4}))
    assert not _event_consumes_shot(ParsedEvent("loot", None, "", {"value": 1}))
