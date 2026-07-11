from __future__ import annotations

import pytest

from ped_hunter.app import (
    PedHunterApp,
    STREAMER_DEFAULT_HEIGHT,
    STREAMER_DEFAULT_WIDTH,
    STREAMER_MIN_HEIGHT,
    STREAMER_MIN_WIDTH,
    _kill_trend_points,
    _kill_trend_summary,
)
from ped_hunter.storage import LoadoutRecord, Store


def test_streamer_window_defaults_are_smaller_than_main_dashboard() -> None:
    assert STREAMER_DEFAULT_WIDTH == 420
    assert STREAMER_DEFAULT_HEIGHT == 300
    assert STREAMER_MIN_WIDTH == 340
    assert STREAMER_MIN_HEIGHT == 250


def test_kill_trend_points_roll_up_cost_profit_and_delta(tmp_path) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    loadout = LoadoutRecord(
        id=None,
        name="Starter rifle",
        weapon="Frontier Rifle",
        ammo_burn=1000,
        decay=0.2,
        cost_per_shot=0.3,
    )
    session_id = store.start_session("hunt", loadout)

    store.add_event(
        session_id,
        {
            "kind": "combat",
            "timestamp": "2026-01-01T10:00:00",
            "raw_message": "shot 1",
            "payload": {"shot_cost": 1.25, "ammo_cost": 0.10, "repair_decay": 0.15},
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "loot",
            "timestamp": "2026-01-01T10:00:00.100000",
            "raw_message": "loot 1",
            "payload": {"value": 4.0, "item_name": "Daikiba"},
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "combat",
            "timestamp": "2026-01-01T10:00:00.200000",
            "raw_message": "shot 2",
            "payload": {"shot_cost": 0.75, "ammo_cost": 0.05, "repair_decay": 0.10},
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "loot",
            "timestamp": "2026-01-01T10:00:00.600000",
            "raw_message": "loot 2",
            "payload": {"value": 1.5, "item_name": "Daikiba Young"},
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "combat",
            "timestamp": "2026-01-01T10:00:01",
            "raw_message": "shot 3",
            "payload": {"shot_cost": 0.50, "ammo_cost": 0.05, "repair_decay": 0.05},
        },
    )
    store.add_event(
        session_id,
        {
            "kind": "loot",
            "timestamp": "2026-01-01T10:00:08",
            "raw_message": "loot 3",
            "payload": {"value": 0.80, "item_name": "Argonaut"},
        },
    )

    points = _kill_trend_points(store, session_id)
    assert [point["kill"] for point in points] == [1, 2, 3]
    assert points[0]["cost"] == 1.25
    assert points[0]["avg_cost"] == 1.25
    assert points[0]["loot"] == pytest.approx(4.0)
    assert points[0]["profit"] == pytest.approx(2.75)
    assert points[0]["trend_symbol"] == "•"
    assert points[1]["cost"] == 0.75
    assert points[1]["loot"] == pytest.approx(1.5)
    assert points[1]["profit"] == pytest.approx(0.75)
    assert points[1]["delta_profit"] == pytest.approx(-2.0)
    assert points[1]["trend_symbol"] == "↓"
    assert points[2]["cost"] == 0.50
    assert points[2]["loot"] == pytest.approx(0.8)
    assert points[2]["profit"] == pytest.approx(0.30)
    assert points[2]["delta_profit"] == pytest.approx(-0.45)
    assert points[2]["trend_symbol"] == "↓"

    summary = _kill_trend_summary(points)
    assert "K3" in summary
    assert "cost 0.50" in summary
    assert "avg 0.83" in summary
    assert "profit +0.30" in summary
    assert "↓" in summary




def test_kill_trend_points_are_not_capped_at_sixteen_kills(tmp_path) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    loadout = LoadoutRecord(
        id=None,
        name="Starter rifle",
        weapon="Frontier Rifle",
        ammo_burn=1000,
        decay=0.2,
        cost_per_shot=0.3,
    )
    session_id = store.start_session("hunt", loadout)

    for i in range(20):
        store.add_event(
            session_id,
            {
                "kind": "combat",
                "timestamp": f"2026-01-01T10:00:{i:02d}",
                "raw_message": f"shot {i + 1}",
                "payload": {"shot_cost": 0.25, "ammo_cost": 0.10, "repair_decay": 0.15},
            },
        )
        store.add_event(
            session_id,
            {
                "kind": "loot",
                "timestamp": f"2026-01-01T10:00:{i:02d}.500000",
                "raw_message": f"loot {i + 1}",
                "payload": {"value": 1.0 + i * 0.1, "item_name": "Daikiba"},
            },
        )

    points = _kill_trend_points(store, session_id)
    assert len(points) == 20
    assert points[0]["kill"] == 1
    assert points[-1]["kill"] == 20

def test_app_defaults_to_last_contributed_session_and_loadout(tmp_path, monkeypatch) -> None:
    store = Store(tmp_path / "ped-hunter.sqlite3")
    loadout_a = LoadoutRecord(
        id=None,
        name="Old rifle",
        weapon="Frontier Rifle",
        ammo_burn=1000,
        decay=0.2,
        cost_per_shot=0.3,
    )
    loadout_b = LoadoutRecord(
        id=None,
        name="Current rifle",
        weapon="Frontier Hunting Rifle",
        ammo_burn=1200,
        decay=0.25,
        cost_per_shot=0.45,
    )
    loadout_a.id = store.save_loadout(loadout_a, make_active=False)
    loadout_b.id = store.save_loadout(loadout_b, make_active=False)
    session_a = store.start_session("hunt", loadout_a)
    session_b = store.start_session("hunt", loadout_b)
    store.add_event(
        session_a,
        {
            "kind": "loot",
            "timestamp": "2026-01-01T10:00:00",
            "raw_message": "loot a",
            "payload": {"value": 1.0, "item_name": "Daikiba"},
        },
    )
    store.add_event(
        session_b,
        {
            "kind": "loot",
            "timestamp": "2026-01-01T10:05:00",
            "raw_message": "loot b",
            "payload": {"value": 2.5, "item_name": "Argonaut"},
        },
    )

    from ped_hunter import app as appmod

    monkeypatch.setattr(appmod, "Store", lambda: store)
    app = PedHunterApp()
    try:
        preferred = store.get_last_contributed_session()
        assert preferred is not None
        assert preferred.session_id == session_b
        assert app._preferred_session() is not None
        assert app._preferred_session().session_id == session_b
        active = store.get_active_loadout()
        assert active is not None
        assert active.name == "Current rifle"
        assert app.hero_session.get().startswith("Hunt · Current rifle")
    finally:
        app.destroy()


def test_streamer_chart_section_renders_before_the_lower_priority_sections_and_fits_contents() -> None:
    app = PedHunterApp()
    try:
        app._open_streamer_window()
        streamer = app.streamer_window
        assert streamer is not None
        pack_order = streamer.outer.pack_slaves()
        assert pack_order.index(streamer.kill_chart.master) < pack_order.index(streamer.durability_bar.master)

        app.update_idletasks()
        assert streamer.winfo_width() == streamer.winfo_reqwidth()
        assert streamer.winfo_height() == streamer.winfo_reqheight()

        long_loadout = LoadoutRecord(
            id=None,
            name="Starter rifle\nline 2 of a deliberately tall streamer loadout\nline 3 of the streamer loadout\nline 4 of the streamer loadout\nline 5 of the streamer loadout\nline 6 of the streamer loadout\nline 7 of the streamer loadout\nline 8 of the streamer loadout",
            weapon="Frontier Hunting Rifle",
            amp="ZX Sinkadus",
            ammo_burn=140,
            decay=0.00052,
            cost_per_shot=0.01452,
        )
        session_id = app.store.start_session("hunt", long_loadout)
        app.store.add_event(
            session_id,
            {
                "kind": "loot",
                "raw_message": "loot 1",
                "payload": {"value": 4.0, "item_name": "Daikiba"},
            },
        )
        session = app.store.get_session(session_id)
        assert session is not None
        streamer.update_from_session(session)
        app.update_idletasks()

        assert streamer.vars["loot"].get() == "4.00 PED"
        assert streamer.vars["cost"].get() == "0.00 PED"
        assert streamer.vars["kills"].get() == "1"

        assert streamer.winfo_width() == streamer.winfo_reqwidth()
        assert streamer.winfo_height() == streamer.winfo_reqheight()

        initial_chart_height = streamer.kill_chart.winfo_height()
        streamer.geometry(f"{streamer.winfo_width()}x{streamer.winfo_height() + 120}")
        app.update()

        assert streamer.kill_chart.winfo_height() > initial_chart_height
    finally:
        app.destroy()
