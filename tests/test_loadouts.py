from pathlib import Path
from types import SimpleNamespace

from ped_hunter.app import (
    AMP_CATEGORIES,
    PedHunterApp,
    SCOPE_CATEGORIES,
    SIGHT_CATEGORIES,
    _attachment_in_categories,
    _attachment_names_by_category,
    _event_consumes_shot,
    _loot_event_points,
    _typeahead_match,
    calculate_blueprint_material_cost,
    calculate_loadout_cost,
    streamer_metrics,
)
from ped_hunter.catalog import BlueprintRecord, Catalog, ResourceRecord
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


def test_blueprint_material_cost_sums_material_tt_values():
    catalog = Catalog(
        weapons={},
        attachments={},
        resources={
            "Metal Residue": ResourceRecord("Metal Residue", 0.01),
            "Lysterium Ingot": ResourceRecord("Lysterium Ingot", 0.03),
        },
        blueprints={
            "Test Widget Blueprint": BlueprintRecord(
                "Test Widget Blueprint",
                (("Metal Residue", 10), ("Lysterium Ingot", 2)),
            )
        },
        aliases={},
    )

    per_attempt, materials = calculate_blueprint_material_cost(catalog, "widget")

    assert round(per_attempt, 2) == 0.16
    assert materials == [
        ("Metal Residue", 10, 0.01, 0.1),
        ("Lysterium Ingot", 2, 0.03, 0.06),
    ]


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
    store.add_event(session_id, {"kind": "loot", "raw_message": "loot", "payload": {"item_name": "Animal Oil Residue", "value": 0.25}})

    summary = store.get_current_session()
    assert summary is not None
    assert summary.loadout_name == "Test Frontier"
    assert summary.hunting_cost == 0.0102
    assert summary.net_value == 0.2398


def test_store_excludes_legacy_refiner_conversion_rows_from_profit(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.add_event(session_id, {"kind": "loot", "raw_message": "real loot", "payload": {"item_name": "Animal Oil Residue", "value": 0.25}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "refined oil", "payload": {"item_name": "Oil", "value": 2.14}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "refined lyst", "payload": {"item_name": "Lysterium Ingot", "value": 8.01}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "ammo conversion", "payload": {"item_name": "Universal Ammo", "value": 293.09}})

    summary = store.get_current_session()

    assert summary is not None
    assert summary.events == 4
    assert summary.loot_value == 0.25
    assert summary.net_value == 0.25


def test_store_summarizes_crafting_material_costs(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.add_event(session_id, {"kind": "craft", "raw_message": "craft cost", "payload": {"blueprint": "Widget Blueprint", "attempts": 3, "total_cost": 12.0}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "loot", "payload": {"value": 5.0}})

    summary = store.get_current_session()

    assert summary is not None
    assert summary.hunting_cost == 12.0
    assert summary.loot_value == 5.0
    assert summary.net_value == -7.0


def test_store_excludes_universal_ammo_conversion_from_session_loot(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.add_event(session_id, {"kind": "combat", "raw_message": "hit", "payload": {"damage": 1, "shot_cost": 10.0}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "real loot", "payload": {"item_name": "Shrapnel", "value": 25.0}})
    store.add_event(session_id, {"kind": "loot", "raw_message": "conversion", "payload": {"item_name": "Universal Ammo", "value": 293.09}})

    summary = store.get_current_session()

    assert summary is not None
    assert summary.loot_value == 25.0
    assert summary.net_value == 15.0


def test_store_summarizes_session_skill_gains_like_lootnanny(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.add_event(session_id, {"kind": "skill", "raw_message": "anatomy", "payload": {"skill": "Anatomy", "xp": 0.25}})
    store.add_event(session_id, {"kind": "skill", "raw_message": "rifle", "payload": {"skill": "Rifle", "xp": 0.75}})
    store.add_event(session_id, {"kind": "skill", "raw_message": "anatomy again", "payload": {"skill": "Anatomy", "xp": 0.50}})
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)",
            (session_id, None, "skill", "legacy bad row", "not json"),
        )
        conn.commit()

    gains = store.skill_gains_for_session(session_id)

    assert [(gain.skill, round(gain.xp, 4), gain.procs, round(gain.proc_pct)) for gain in gains] == [
        ("Anatomy", 0.75, 2, 67),
        ("Rifle", 0.75, 1, 33),
    ]


def test_lifetime_totals_are_weighted_across_all_sessions(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    large_session = store.start_session("hunt")
    store.add_event(large_session, {"kind": "combat", "raw_message": "cost", "payload": {"damage": 1, "shot_cost": 100.0}})
    store.add_event(large_session, {"kind": "loot", "raw_message": "loot", "payload": {"value": 90.0}})
    store.end_session(large_session)

    small_session = store.start_session("hunt")
    store.add_event(small_session, {"kind": "combat", "raw_message": "cost", "payload": {"damage": 1, "shot_cost": 10.0}})
    store.add_event(small_session, {"kind": "loot", "raw_message": "loot", "payload": {"value": 20.0}})
    store.end_session(small_session)

    totals = store.lifetime_totals()

    assert totals.session_count == 2
    assert totals.active_count == 0
    assert totals.total_cost == 110.0
    assert totals.total_loot == 110.0
    assert totals.total_net == 0.0
    assert totals.overall_return_pct == 100.0
    assert totals.avg_return_pct == 145.0
    assert totals.avg_profit_per_run == 0.0
    assert totals.best_session is not None
    assert totals.best_session.session_id == small_session
    assert totals.worst_session is not None
    assert totals.worst_session.session_id == large_session


def test_store_can_resume_ended_session(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.end_session(session_id)
    ended = store.get_session(session_id)
    assert ended is not None
    assert ended.ended_at is not None

    store.resume_session(session_id)

    resumed = store.get_session(session_id)
    assert resumed is not None
    assert resumed.ended_at is None
    assert store.get_current_session().session_id == session_id


def test_display_session_prefers_explicit_resumed_session(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    resumed_id = store.start_session("hunt")
    store.add_event(resumed_id, {"kind": "loot", "raw_message": "old", "payload": {"value": 1.0}})
    other_active_id = store.start_session("hunt")
    store.add_event(other_active_id, {"kind": "loot", "raw_message": "new", "payload": {"value": 9.0}})
    app_like = SimpleNamespace(session_id=resumed_id, store=store)

    display = PedHunterApp._display_session(app_like, store.get_session(other_active_id), store.list_recent_sessions())

    assert display is not None
    assert display.session_id == resumed_id
    assert display.loot_value == 1.0


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


def test_store_quarantines_and_recreates_malformed_database(tmp_path: Path):
    db_path = tmp_path / "ped.sqlite3"
    db_path.write_bytes(b"this is not a sqlite database")

    store = Store(db_path)

    assert store.recovery_message is not None
    backups = list(tmp_path.glob("ped.sqlite3.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"this is not a sqlite database"
    assert store.list_recent_sessions() == []


def test_loot_event_points_are_chronological_and_skip_bad_rows(tmp_path: Path):
    store = Store(tmp_path / "ped.sqlite3")
    session_id = store.start_session("hunt")
    store.add_event(session_id, {"kind": "loot", "timestamp": "t1", "raw_message": "loot", "payload": {"item_name": "Animal Oil", "value": 0.06}})
    store.add_event(session_id, {"kind": "combat", "timestamp": "t2", "raw_message": "hit", "payload": {"damage": 2}})
    store.add_event(session_id, {"kind": "loot", "timestamp": "t3", "raw_message": "loot", "payload": {"item_name": "Shrapnel", "value": 1.25}})
    store.add_event(session_id, {"kind": "loot", "timestamp": "t4", "raw_message": "conversion", "payload": {"item_name": "Universal Ammo", "value": 293.09}})
    store.add_event(session_id, {"kind": "loot", "timestamp": "t5", "raw_message": "refined oil", "payload": {"item_name": "Oil", "value": 2.14}})
    store.add_event(session_id, {"kind": "loot", "timestamp": "t6", "raw_message": "refined lyst", "payload": {"item_name": "Lysterium Ingot", "value": 8.01}})
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO events (session_id, timestamp, kind, raw_message, payload) VALUES (?, ?, ?, ?, ?)",
            (session_id, "t7", "loot", "legacy bad row", "not json"),
        )
        conn.commit()

    assert _loot_event_points(store, session_id) == [
        ("t1", 0.06, "(Animal Oil)"),
        ("t3", 1.25, "(Shrapnel)"),
    ]


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
