from ped_hunter.currency import format_money
import pytest


def test_usd_mode_moves_the_ped_decimal_one_place():
    assert format_money(100.0, "USD") == "$10.00"
    assert format_money(12.5, "USD") == "$1.25"
    assert format_money(12.5, "USD", signed=True) == "+$1.25"
    assert format_money(-12.5, "USD", signed=True) == "-$1.25"


def test_ped_mode_remains_available():
    assert format_money(12.5, "PED") == "12.50 PED"
    assert format_money(-12.5, "PED", signed=True) == "-12.50 PED"


@pytest.mark.parametrize("change_path", ["variable", "selector"])
def test_currency_changes_refresh_rendered_money_without_repricing(tmp_path, monkeypatch, change_path):
    from copy import deepcopy
    from dataclasses import asdict
    from tkinter import ttk

    from ped_hunter import app as appmod
    from ped_hunter.storage import LoadoutRecord, Store

    chat = tmp_path / "synthetic-chat.log"
    chat.write_text("")
    monkeypatch.setattr(appmod, "_default_chat_log_path", lambda: chat)
    store = Store(tmp_path / "currency.sqlite3")
    gear = LoadoutRecord(None, "Currency fixture", "Frontier Rifle",
                         ammo_burn=100, decay=0.0002, cost_per_shot=0.0102)
    store.save_loadout(gear, make_active=True)
    store.set_selected_healing_tool("Frontier First Aid Pack, Adjusted")
    sid = store.start_session("hunt", gear)
    # A recorded historical price must survive display changes, not be
    # replaced with the currently selected setup's catalog quote.
    store.add_event(sid, {"kind": "combat", "raw_message": "Synthetic recorded input",
                          "payload": {"damage": 6, "shot_cost": 2.5}})
    store.add_event(sid, {"kind": "loot", "timestamp": "2026-11-12T12:00:00",
                          "raw_message": "Synthetic loot",
                          "payload": {"value": 100.0, "item_name": "Shrapnel"}})
    app = appmod.PedHunterApp(store=store)
    app.title("PED Hunter isolated currency integration")
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    try:
        app.reduced_motion.set(True)
        assert app.currency_mode.get() == "USD"
        assert app.hero_loot.get() == "$0.00"
        app._process_lines(["2026-11-12 12:00:04 [System] [] You healed yourself 23.0 points"], sid, None)
        app.session_id = sid
        app.event_filter.set("Everything")
        app._refresh_all()
        app._open_streamer_window()
        hud = app.streamer_window
        hud.title("PED Hunter isolated currency HUD")
        app.update()
        picker = app.currency_picker  # under Help & settings
        before = asdict(store.get_session(sid))
        with store.connect() as conn:
            original_rows = [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
            ids = {row["kind"]: str(row["id"]) for row in conn.execute("SELECT id,kind FROM events ORDER BY id")}
        points = deepcopy(hud.kill_trend_points)

        def change(mode):
            if change_path == "variable":
                app.currency_mode.set(mode)
            else:
                picker.current(1 if mode == "PED" else 0)
                picker.event_generate("<<ComboboxSelected>>")

        for mode, loot, cost, net, healing in (
            ("PED", "100.00 PED", "2.52 PED", "+97.48 PED", "0.02000 PED"),
            ("USD", "$10.00", "$0.25", "+$9.75", "$0.00200"),
            ("PED", "100.00 PED", "2.52 PED", "+97.48 PED", "0.02000 PED"),
        ):
            change(mode)  # No explicit refresh, second toggle or ingestion tick.
            assert (app.hero_loot.get(), app.hero_cost.get(), app.hero_net.get()) == (loot, cost, net)
            assert (app.lifetime_vars["total_loot"].get(), app.lifetime_vars["total_cost"].get(),
                    app.lifetime_vars["total_net"].get()) == (loot, cost, net)
            assert (hud.vars["loot"].get(), hud.vars["cost"].get(), hud.vars["net_big"].get()) == (loot, cost, net)
            values = app.sessions_tree.item(sid, "values")
            assert all(value in values for value in (loot, cost, net))
            assert loot in str(app.events_tree.item(ids["loot"], "values"))
            assert healing in str(app.events_tree.item(ids["combat"], "values"))
            assert healing in app.healing_totals.get()
            assert app.healing_tool.get() == "Frontier First Aid Pack, Adjusted"
            assert asdict(store.get_session(sid)) == before
            assert hud.kill_trend_points == points
            with store.connect() as conn:
                assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == original_rows
            assert not errors

        app.session_id = None
        app._refresh_all()
        assert app.hero_loot.get() == "0.00 PED"
        change("USD")
        assert app.hero_loot.get() == "$0.00"
        assert "$10.00" in app.sessions_tree.item(sid, "values")
    finally:
        app._stop_log_worker()
        app.animator.cancel_all()
        app.destroy()


@pytest.mark.parametrize("change_path", ["variable", "selector"])
def test_manufacturing_preview_refreshes_small_input_prices_without_rebooking(tmp_path, monkeypatch, change_path):
    from dataclasses import asdict
    import json
    from tkinter import ttk

    from ped_hunter import app as appmod
    from ped_hunter.storage import Store

    chat = tmp_path / "synthetic-chat.log"
    chat.write_text("")
    monkeypatch.setattr(appmod, "_default_chat_log_path", lambda: chat)
    store = Store(tmp_path / "manufacturing-currency.sqlite3")
    sid = store.start_session("craft")
    app = appmod.PedHunterApp(store=store)
    app.title("PED Hunter isolated Compound B currency regression")
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    try:
        app.reduced_motion.set(True)
        app.session_id = sid
        app.currency_mode.set("USD")
        app.crafting_attempts.set("9")
        app.crafting_blueprint.set("Muscle Oil Compound B Blueprint")
        app._refresh_crafting_preview()  # Initial blueprint/attempt edit only.
        app._add_crafting_cost_to_session()
        app.manufacturing_tab.master.select(app.manufacturing_tab)
        app.geometry(f"{round(760 * app.display_scale)}x{round(500 * app.display_scale)}")
        app.update()
        before = asdict(store.get_session(sid))
        assert before["hunting_cost"] == pytest.approx(0.27225)
        with store.connect() as conn:
            original_rows = [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")]
            booked = json.loads(conn.execute("SELECT payload FROM events WHERE kind='manufacturing_offset'").fetchone()[0])
        assert booked["attempts_total"] == 9
        assert booked["cost_per_attempt"] == pytest.approx(0.03025)
        assert booked["total_cost"] == pytest.approx(0.27225)
        picker = app.currency_picker  # under Help & settings
        for mode, per_attempt, total in (
            ("USD", "$0.003025", "$0.027225"),
            ("PED", "0.03025 PED", "0.27225 PED"),
            ("USD", "$0.003025", "$0.027225"),
            ("PED", "0.03025 PED", "0.27225 PED"),
            ("USD", "$0.003025", "$0.027225"),
        ):
            if change_path == "variable":
                app.currency_mode.set(mode)
            else:
                picker.current(0 if mode == "USD" else 1)
                picker.event_generate("<<ComboboxSelected>>")
            # No preview refresh, blueprint/attempt edit, ingestion or second toggle.
            preview = app.crafting_preview.get()
            assert f"{per_attempt}/attempt" in preview
            assert f"9 attempts = {total} input" in preview
            app.update()
            label = app.crafting_preview_label
            reference = ttk.Label(app, text=preview, style="Panel.TLabel", font=label.cget("font"),
                                  wraplength=max(1, label.winfo_width() - 2))
            try:
                assert label.winfo_height() >= reference.winfo_reqheight()
            finally:
                reference.destroy()
            assert app.crafting_blueprint.get() == "Muscle Oil Compound B Blueprint"
            assert app.crafting_attempts.get() == "9"
            assert asdict(store.get_session(sid)) == before
            with store.connect() as conn:
                assert [tuple(row) for row in conn.execute("SELECT * FROM events ORDER BY id")] == original_rows
            assert app._log_worker is None
            assert not errors
    finally:
        app._stop_log_worker()
        app.animator.cancel_all()
        app.destroy()