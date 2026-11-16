import sqlite3
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
sid = conn.execute("SELECT session_id FROM events GROUP BY session_id ORDER BY MAX(id) DESC LIMIT 1").fetchone()[0]
conn.close()
S = app.display_scale
log("session", sid, "scale", S, "minsize", app.minsize(), "geom", app.geometry(), "class", app.size_class)
shot("00-launch-idle-default")
app.session_id = sid
app._refresh_all()
pump(800)


def tabs(prefix, names):
    for name in names:
        try:
            tab(name)
        except KeyError:
            tab("Help" if name.startswith("Help") else "Help & settings")
        shot(f"{prefix}-{name.split()[0].lower()}")


ALL = ("Runs", "Lifetime", "Events", "Skills", "Setups", "Crafting", "WTB", "Help & settings")
tabs("01-default", ALL)
log("class", app.size_class)
size(1180 * S, 780 * S)
log("class", app.size_class)
tabs("02-wide", ("Runs", "Events", "Setups"))
size(700 * S, 560 * S)
log("class", app.size_class)
tabs("03-compact", ("Runs", "Events", "Skills", "Setups", "Lifetime"))
app._toggle_drawer(True); pump(300)
shot("03-compact-drawer")
app._toggle_drawer(False)
size(520 * S, 360 * S)
log("class", app.size_class, "geometry", app.geometry())
tabs("04-min", ALL)
app._toggle_chart_first(); pump(400)
shot("04-min-chart")
app._toggle_chart_first()
app._toggle_drawer(True); pump(300)
shot("04-min-drawer")
app._toggle_drawer(False)
size(560 * S, 900 * S)
log("class", app.size_class)
tabs("05-narrow-tall", ("Runs", "Skills"))
size(960 * S, 620 * S)
tab("Events")
app.event_filter.set("Loot"); app._event_filter_changed(); pump(300)
first = app.events_tree.get_children()[0]
app.events_tree.selection_set(first); pump(200)
shot("06-events-loot-selected")
app._exclude_selected_loot(); pump(700)
shot("06-loot-corrections", app._event_history_window)
app._event_history_window.destroy()
app._open_weapon_data(); pump(500)
shot("07-weapon-data", app.catalog_window)
app.catalog_window.withdraw()
