import sqlite3
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
sid = conn.execute("SELECT session_id FROM events GROUP BY session_id ORDER BY MAX(id) DESC LIMIT 1").fetchone()[0]
conn.close()
S = app.display_scale
app.session_id = sid
app._refresh_all()
pump(600)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    log(("PASS " if ok else "FAIL ") + name, detail)


def keys(widget, text):
    for ch in text:
        sym = {" ": "space"}.get(ch, ch)
        widget.event_generate("<KeyPress>", keysym=sym)
        widget.event_generate("<KeyRelease>", keysym=sym)
        pump(30)


def key(widget, sym):
    widget.event_generate(f"<KeyPress-{sym}>")
    widget.event_generate(f"<KeyRelease-{sym}>")
    pump(60)


# ---- searchable weapon selector -------------------------------------------
tab("Setups")
weapon = app.loadout_fields["Weapon"]
app.loadout_name.set("Test setup")
weapon.event_generate("<ButtonPress-1>", x=20, y=10); weapon.event_generate("<ButtonRelease-1>", x=20, y=10); pump(200)
check("click opens the full list", weapon.is_open() and len(weapon._shown) == len(app.catalog.weapons), len(weapon._shown))
shot("10-weapon-list-open", weapon._popup)
weapon.focus_force(); pump(50)
keys(weapon, "fron rif")
check("typing filters by every word", weapon.is_open() and 0 < len(weapon._shown) < 40
      and all("fron" in v.casefold() and "rif" in v.casefold() for v in weapon._shown), weapon._shown[:6])
shot("10-weapon-list-filtered", weapon._popup)
shot("10-setups-typing")
first, second = weapon._shown[0], weapon._shown[min(1, len(weapon._shown) - 1)]
key(weapon, "Down")
check("Down moves the highlight", weapon.highlighted() == second, weapon.highlighted())
key(weapon, "Return")
check("Enter picks the highlighted weapon", not weapon.is_open() and app.loadout_weapon.get() == second, app.loadout_weapon.get())
check("cost preview follows the choice", "Cost/shot" in app.loadout_preview.get(), app.loadout_preview.get())
# no match
weapon.event_generate("<ButtonPress-1>", x=20, y=10); pump(100)
weapon.delete(0, "end"); keys(weapon, "zzzq")
check("no match shows a disabled row", weapon.is_open() and weapon._shown == [] and weapon._listbox.get(0) == "No match")
key(weapon, "Return")
check("Enter with no match changes nothing", app.loadout_weapon.get() == "zzzq" and weapon.is_open())
key(weapon, "Escape")
check("Esc closes and restores the last choice", not weapon.is_open() and app.loadout_weapon.get() == second, app.loadout_weapon.get())
# invalid text on blur
weapon.delete(0, "end"); weapon.insert(0, "not a weapon")
weapon._settle(); pump(50)
check("leaving with unknown text restores the last choice", app.loadout_weapon.get() == second, app.loadout_weapon.get())
# mouse pick from the list
weapon.event_generate("<ButtonPress-1>", x=20, y=10); pump(100)
weapon.delete(0, "end"); keys(weapon, "frontier")
target = weapon._shown[0]
box = weapon._listbox.bbox(0)
weapon._listbox.event_generate("<ButtonPress-1>", x=box[0] + 5, y=box[1] + 3); pump(100)
check("clicking a row picks it", app.loadout_weapon.get() == target and not weapon.is_open(), target)
# amplifier: empty text settles on None; arrow click toggles
amp = app.loadout_fields["Amplifier"]
amp.event_generate("<ButtonPress-1>", x=amp.winfo_width() - 8, y=10); pump(100)
check("arrow button opens", amp.is_open())
amp.event_generate("<ButtonPress-1>", x=amp.winfo_width() - 8, y=10); pump(100)
check("arrow button closes", not amp.is_open())
amp.delete(0, "end"); amp._settle()
check("empty attachment becomes None", app.loadout_amp.get() == "None", app.loadout_amp.get())
log("arrow element", amp.identify(amp.winfo_width() - 8, 10), "arrow px", amp.winfo_width() - amp.bbox(0)[0] if False else "")
# healing selector uses the same widget and saves the choice
heal = app.healing_selector
before = app.healing_tool.get()
heal.event_generate("<ButtonPress-1>", x=10, y=10); pump(100)
keys(heal, "")
check("healing selector opens with its list", heal.is_open() and len(heal._shown) > 3, len(heal._shown))
key(heal, "Escape")
check("healing unchanged after Esc", app.healing_tool.get() == before and app.store.get_selected_healing_tool() == (None if before == "None" else before))
app._new_loadout_draft()

# ---- sorting ---------------------------------------------------------------
tab("Skills")
tree = app.skills_tree


def click_heading(tree, column):
    cols = tree.cget("displaycolumns")
    cols = list(tree.cget("columns")) if cols in ("#all", ("#all",)) else list(cols)
    x = 0
    for name in cols:
        width = int(tree.column(name, "width"))
        if name == column:
            x += width // 2
            break
        x += width
    assert tree.identify_region(x, 6) == "heading", tree.identify_region(x, 6)
    tree.event_generate("<ButtonPress-1>", x=x, y=6); tree.event_generate("<ButtonRelease-1>", x=x, y=6); pump(150)


def column(tree, name):
    return [tree.set(iid, name) for iid in tree.get_children()]


num = lambda text: float(str(text).replace(",", "").replace("%", "").replace("$", "").replace("+", ""))
xp = [num(v) for v in column(tree, "xp")]
check("skills default: XP gained high to low", xp == sorted(xp, reverse=True) and "▾" in tree.heading("xp", "text"))
click_heading(tree, "proc_pct")
pct = [num(v) for v in column(tree, "proc_pct")]
check("click Proc %: highest first", pct == sorted(pct, reverse=True) and "▾" in tree.heading("proc_pct", "text")
      and "▾" not in tree.heading("xp", "text"), (column(tree, "skill")[0], pct[:3]))
shot("11-skills-sorted-proc-desc")
click_heading(tree, "proc_pct")
pct = [num(v) for v in column(tree, "proc_pct")]
check("click again: lowest first", pct == sorted(pct) and "▴" in tree.heading("proc_pct", "text"), pct[:3])
click_heading(tree, "skill")
names = column(tree, "skill")
check("click Skill: A to Z", names == sorted(names, key=str.casefold), names[:3])
click_heading(tree, "procs")
app._refresh_skills(sid); pump(100)
procs = [num(v) for v in column(tree, "procs")]
check("sort survives a refresh", procs == sorted(procs, reverse=True) and "▾" in tree.heading("procs", "text"), procs[:3])
tab("Runs")
click_heading(app.sessions_tree, "net")
nets = [num(v) for v in column(app.sessions_tree, "net")]
check("runs sort by Net high to low", nets == sorted(nets, reverse=True), nets[:3])
shot("11-runs-sorted-net")
click_heading(app.sessions_tree, "started")
click_heading(app.sessions_tree, "started")
starts = [app.sessions_tree.sort_keys[(iid, "started")] for iid in app.sessions_tree.get_children()]
check("runs sort by date uses real timestamps", starts == sorted(starts, reverse=True), starts[:2])

# ---- events filter and corrections count -----------------------------------
tab("Events")
for choice, kinds in (("Loot", {"Loot"}), ("Combat", {"Combat"}), ("Globals", {"Global"}), ("Loot and skills", {"Loot", "Skill"})):
    app.event_filter.set(choice); app._event_filter_changed(); pump(100)
    seen = set(column(app.events_tree, "kind"))
    check(f"events filter {choice}", seen and seen <= kinds, seen)
app.event_filter.set("Loot"); app._event_filter_changed(); pump(100)
check("no loot selected: button disabled", str(app.exclude_loot_button.cget("state")) == "disabled")
app.events_tree.selection_set(app.events_tree.get_children()[0]); pump(100)
check("loot selected: button enabled", str(app.exclude_loot_button.cget("state")) == "normal")
check("corrections button shows count", "1 left out" in app.corrections_text.get(), app.corrections_text.get())

# ---- numbers unchanged -----------------------------------------------------
s = app.store.get_session(sid)
check("readouts equal stored totals", app.hero_net.get() == app._money(s.net_value, signed=True)
      and app.hero_loot.get() == app._money(s.loot_value) and app.hero_cost.get() == app._money(s.hunting_cost),
      (app.hero_net.get(), app.hero_loot.get(), app.hero_cost.get(), app.hero_return.get(), app.hero_burn.get()))

# ---- tracking state ---------------------------------------------------------
app.session_id = None
app.start(); pump(900)
check("New run starts tracking", app.running and str(app.stop_button.cget("state")) == "normal")
check("rail shows only End run while tracking", app.stop_button.winfo_ismapped() and not app.start_button.winfo_ismapped())
tab("Runs")
shot("12-tracking-regular")
size(560 * S, 400 * S)
check("run bar mirrors tracking", app.bar_stop.winfo_ismapped() and not app.bar_start.winfo_ismapped() and app.bar_state.winfo_ismapped())
shot("12-tracking-min")
app.stop(); pump(500)
check("End run returns to New run / Resume", not app.running and app.bar_start.winfo_ismapped() and not app.bar_stop.winfo_ismapped())
app._toggle_top_area(); pump(200)
check("Hide totals hides the strip only", not app.hero_panel.winfo_ismapped() and app.controls_panel.winfo_ismapped())
app._toggle_top_area(); pump(200)

# ---- resize sweep: nothing errors, classes change at thresholds only --------
import time
classes, costs = [], []
for step in range(41):
    w = 1180 - (1180 - 520) * step / 40
    h = 780 - (780 - 360) * step / 40
    t = time.perf_counter()
    app.geometry(f"{round(w * S)}x{round(h * S)}"); app.update()
    costs.append((time.perf_counter() - t) * 1000)
    if not classes or classes[-1][1] != app.size_class:
        classes.append((f"{round(w)}x{round(h)}", app.size_class))
log("resize sweep ms: median", round(sorted(costs)[len(costs) // 2], 1), "max", round(max(costs), 1), "sum", round(sum(costs)))
for entry in classes:
    log("  class change at", *entry)
check("three class changes across the sweep", 3 <= len(classes) <= 6, len(classes))
log("RESULT", sum(results), "of", len(results), "passed")
