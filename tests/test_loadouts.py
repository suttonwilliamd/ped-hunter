from pathlib import Path

from ped_hunter.app import (
    AMP_CATEGORIES,
    SCOPE_CATEGORIES,
    SIGHT_CATEGORIES,
    _attachment_in_categories,
    _attachment_names_by_category,
    _event_consumes_shot,
    _typeahead_match,
    calculate_loadout_cost,
    streamer_metrics,
)
from ped_hunter.catalog import Catalog
from ped_hunter.parser import ParsedEvent
from ped_hunter.storage import LoadoutRecord, SessionSummary, Store


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


def test_loadout_typeahead_prefix_matching():
    values = ["None", "ZX Eagle Eye", "ZX R-Dod", "ZX Sinkadus"]
    assert _typeahead_match(values, "z") == "ZX Eagle Eye"
    assert _typeahead_match(values, "zx s") == "ZX Sinkadus"
    assert _typeahead_match(values, "ZX R") == "ZX R-Dod"
    assert _typeahead_match(values, "missing") is None


def test_loadout_selectors_do_not_overlap_attachment_categories():
    catalog = Catalog.load()
    amp_names = _attachment_names_by_category(catalog, AMP_CATEGORIES)
    scope_names = _attachment_names_by_category(catalog, SCOPE_CATEGORIES)
    sight_names = _attachment_names_by_category(catalog, SIGHT_CATEGORIES)

    assert "ZX Sinkadus" in amp_names
    assert "ZX Sinkadus" not in scope_names
    assert "ZX Sinkadus" not in sight_names
    assert "ZX Eagle Eye" in scope_names
    assert "ZX Eagle Eye" not in amp_names
    assert "ZX R-Dod" in sight_names
    assert "ZX R-Dod" not in amp_names
    assert _attachment_in_categories(catalog, "ZX Eagle Eye", SCOPE_CATEGORIES)
    assert not _attachment_in_categories(catalog, "ZX Eagle Eye", AMP_CATEGORIES)


def test_streamer_metrics_from_session_summary():
    metrics = streamer_metrics(
        SessionSummary(
            session_id="ph-test",
            started_at="2026-01-01T00:00:00",
            ended_at=None,
            activity="hunt",
            loot_value=75.0,
            combat_damage=123.4,
            hunting_cost=100.0,
            net_value=-25.0,
            events=42,
            loadout_name="ZX Test Loadout",
        )
    )
    assert metrics["return_pct"] == 75.0
    assert metrics["net"] == -25.0
    assert metrics["loadout"] == "ZX Test Loadout"
