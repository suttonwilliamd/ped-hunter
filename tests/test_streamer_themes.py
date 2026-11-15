"""Themed streamer HUD: shared numbers, clean HUD text, fixed geometry, real Tk behaviour."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from ped_hunter.streamer_model import Diagnostics, Setup, ThemePreference, project
from ped_hunter.streamer_scene import DESIGN, THEMES, scene

FORBIDDEN = re.compile(r"≈|~|\bunknown\b|uncertain|unresolved|\bReview\b|unpriced|\*", re.IGNORECASE)


class Pts(list):
    pass


def session(**kw):
    base = dict(session_id="s1", started_at="2026-11-15T00:00:00", ended_at=None, activity="hunt",
                loot_value=52.0, combat_damage=0.0, hunting_cost=48.0, net_value=4.0, events=10,
                loadout_name="Frontier + ZX", loadout_snapshot={}, repair_shots=0, repair_decay=0.0,
                healing_cost=0.0, estimated_healing_uses=0, unpriced_heal_events=0, accounting_revision=3)
    base.update(kw)
    return SimpleNamespace(**base)


def points(n=8, *, craft=False, unknown=0, excess=None, **attrs):
    out = Pts()
    for i in range(n):
        missing = craft and i >= n - unknown
        out.append(dict(event_id=i + 1, label=("C" if craft else "K") + str(i + 1),
                        kind="craft" if craft else "hunt", loot=0.5 + i / 10, cost=None if missing else 0.7,
                        avg_cost=0.7, loot_events=1, profit=None if missing else i / 10 - 0.2,
                        delta_profit=0.1, excess_input_estimate=excess if missing else None))
    for key, value in attrs.items():
        setattr(out, key, value)
    return out


SETUPS = (Setup(1, "ark"), Setup(2, "Frontier + ZX", True), Setup(3, "bpp"), Setup(4, "ad"))
PLAN = SimpleNamespace(active=True, blueprint="Simple 1 Plastic Springs Blueprint", observed=17, attempts=15,
                       per_attempt=0.91, booked_cost=13.65, returns=12.0, event_id=99)


def cases():
    return {
        "hunt": project(session(), points(), SETUPS, running=True, durability=("Gun durability 94.6% • ~9,463 shots left", 94.6)),
        "healing": project(session(healing_cost=1.3, estimated_healing_uses=9, unpriced_heal_events=2), points(),
                           SETUPS, durability=("Durability 40.0%", 40.0)),
        "craft-overrun": project(session(loot_value=12.0, hunting_cost=13.65),
                                 points(17, craft=True, unknown=2, manufacturing=PLAN, has_crafting=True,
                                        unpriced_heals=1, healing_pending=0.02), SETUPS, unit="PED"),
        "none": project(None, [], SETUPS),
        "zero-cost": project(session(hunting_cost=0.0, loot_value=5.0), points(0), SETUPS),
    }


def hud_text(view):
    return [i.text for i in view.items if i.kind == "text"]


@pytest.mark.parametrize("case", list(cases()))
def test_every_theme_shows_identical_numbers_and_no_uncertainty_language(case):
    model = cases()[case]
    numbers = None
    for theme in THEMES:
        view = scene(model, theme)
        assert numbers is None or view.numbers == numbers
        numbers = view.numbers
        for text in hud_text(view):
            assert not FORBIDDEN.search(text), (theme, text)


def test_estimates_match_legacy_arithmetic_and_estimate_unknown_groups_once():
    hunt = cases()["hunt"]
    assert hunt.get("cost").value == pytest.approx(48.0)
    assert hunt.get("net").value == pytest.approx(4.0)
    assert hunt.get("rate").value == pytest.approx(100 * 52 / 48)
    craft = cases()["craft-overrun"]
    # booked 13.65 + two unevidenced groups at the session's mean priced group cost (0.7)
    assert craft.get("cost").value == pytest.approx(13.65 + 2 * 0.7)
    assert craft.get("cost").classification == "estimated"
    assert any("mean priced craft-group cost" in a for a in craft.diagnostics["assumptions"])
    evidenced = project(session(loot_value=12.0, hunting_cost=13.65),
                        points(4, craft=True, unknown=2, excess=0.5, manufacturing=PLAN), SETUPS)
    assert evidenced.get("cost").value == pytest.approx(13.65 + 2 * 0.5)  # supported excess, once


def test_missing_values_are_not_fabricated():
    assert cases()["none"].get("net").value is None
    assert cases()["none"].get("net").displayed == "—"
    zero = cases()["zero-cost"]
    assert zero.get("cost").value == 0 and zero.get("rate").value is None  # no fake 0% / infinite
    bad = project(session(loot_value=float("nan")), points(), SETUPS)
    assert bad.get("loot").value == 0 and any("invalid" in a for a in bad.diagnostics["assumptions"])


def test_healing_is_shown_but_never_added_to_cost_twice():
    plain, healing = cases()["hunt"], cases()["healing"]
    assert healing.get("cost").value == plain.get("cost").value
    assert healing.get("healing").value == pytest.approx(1.3)
    assert healing.activity[2].startswith("Healing $0.13 · 9 uses")
    assert any("2 healing observations" in a for a in healing.diagnostics["assumptions"])


@pytest.mark.parametrize("theme", list(THEMES))
def test_geometry_is_fixed_when_healing_or_crafting_appears(theme):
    def layout(model):
        view = scene(model, theme)
        targets = [(i.kind, i.xy) for i in view.items if i.target or i.kind != "text"
                   and i.affordance]
        return view.anchors, (view.width, view.height), targets
    base = layout(cases()["hunt"])
    assert layout(cases()["healing"]) == base
    assert layout(cases()["craft-overrun"])[0] == base[0]
    assert layout(cases()["none"])[1] == base[1]


@pytest.mark.parametrize("theme", list(THEMES))
def test_setup_tiles_outline_active_and_footer_is_clickable(theme):
    view = scene(cases()["hunt"], theme, focus="context")
    tiles = [i for i in view.items if i.target.startswith("setup:") and i.kind != "text" and i.outline]
    assert len(tiles) == 4
    active = [t for t in tiles if t.width == 3]
    assert len(active) == 1 and active[0].target == "setup:1"
    footer = [i for i in view.items if i.target == "context" and i.kind == "text"]
    assert footer and footer[0].underline and footer[0].text == "Frontier + ZX"
    assert any(i.affordance == "context-open" for i in view.items)
    assert [c.target for c in view.controls if c.focused] == ["context"]


def test_offscreen_render_all_themes(tmp_path):
    pytest.importorskip("PIL")
    from ped_hunter.streamer_render import rasterize
    for theme in THEMES:
        image, audit = rasterize(scene(cases()["craft-overrun"], theme), tmp_path / f"{theme}.png")
        assert image.size == DESIGN
        assert all(a["fits"] for a in audit), [a["source"] for a in audit if not a["fits"]]


def test_theme_preference_default_roundtrip_and_invalid(tmp_path):
    from ped_hunter.storage import Store
    store = Store(tmp_path / "pref.sqlite3")
    pref = ThemePreference(store)
    assert pref.load() == "current"
    pref.save("vector-beacon")
    assert ThemePreference(store).load() == "vector-beacon"
    with pytest.raises(ValueError):
        pref.save("nope")
    with store.connect() as conn:
        conn.execute("UPDATE app_settings SET value='corrupt' WHERE key=?", (ThemePreference.KEY,))
        conn.commit()
    assert pref.load() == "current"


def test_diagnostics_coalesce_and_carry_provenance():
    records = []
    diag = Diagnostics(records.append)
    model = cases()["craft-overrun"]
    diag.update(model, "SETUP\nctx")
    diag.update(model, "SETUP\nctx")
    assert len(records) == 1
    record = records[0]
    assert record["policy"] == "best-estimate/3" and record["operands"]["unknown_groups"] == 2
    assert "ESTIMATES" in diag.text and "SETUP" in diag.text
    json.dumps(record)


# ---- real Tk window --------------------------------------------------------------
@pytest.fixture
def hud(tmp_path):
    from ped_hunter.app import PedHunterApp
    from ped_hunter.storage import LoadoutRecord, Store
    store = Store(tmp_path / "gui.sqlite3")
    for name in ("ark", "Frontier + ZX"):
        store.save_loadout(LoadoutRecord(None, name, "Frontier Hunting Rifle, Adjusted"))
    app = PedHunterApp(store=store)
    app.reduced_motion.set(True)
    sid = app.store.start_session("hunt")
    app.session_id, app.running = sid, True
    app._open_streamer_window()
    app.update()
    yield app, app.streamer_window, sid
    app.destroy()


def test_window_opens_at_legacy_footprint_and_switching_preserves_session(hud):
    app, window, sid = hud
    scale = window.ui_scale
    assert abs(window.winfo_width() - round(360 * scale)) <= 2
    assert abs(window.winfo_height() - round(330 * scale)) <= 2
    before = app.store.get_session(sid)
    for _ in THEMES:
        window.cycle_theme(1)
        app.update()
    assert window.theme == "current"
    window.select_theme("inkwing")
    assert ThemePreference(app.store).load() == "inkwing"
    assert app.store.get_session(sid) == before
    assert app.running and app.session_id == sid


def test_healing_does_not_resize_window(hud):
    app, window, sid = hud
    size = (window.winfo_width(), window.winfo_height())
    window.update_from_session(app.store.get_session(sid))
    app.update()
    window.update_from_session(session(session_id=sid, healing_cost=2.0, estimated_healing_uses=4))
    app.update()
    assert (window.winfo_width(), window.winfo_height()) == size
    assert window.model.activity[2].startswith("Healing")


def test_setup_click_footer_keyboard_and_live_writes(hud, monkeypatch):
    app, window, sid = hud
    calls = []
    monkeypatch.setattr(window._hunt_attribution, "update",
                        lambda s, p, live: (calls.append(live), p)[1])
    window.update_from_session(app.store.get_session(sid))
    assert calls[-1] is True  # live attribution recorded while tracking, like the legacy HUD
    first = window.model.setups[0]
    window._activate("setup:0")
    app.update()
    assert app.store.get_active_loadout().id == first.id
    window.focus_target = "context"
    window._key_activate()
    app.update()
    assert window._context_dialog.winfo_exists()
    assert "ESTIMATES" in window._context_dialog._context_text.get("1.0", "end")


def test_estimate_log_written_next_to_database(hud):
    app, window, sid = hud
    window.update_from_session(app.store.get_session(sid))
    from ped_hunter.streamer_window import _estimate_logger
    for handler in _estimate_logger(app.store).handlers:
        handler.flush()
    log = app.store.db_path.parent / "logs" / "streamer-estimates.jsonl"
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["session"] == sid and record["policy"] == "best-estimate/3"


def test_trail_storage_failure_keeps_points_and_totals(hud, monkeypatch):
    app, window, sid = hud

    def unavailable(*_args, **_kwargs):
        raise OSError("isolated trail storage failure")

    monkeypatch.setattr(app.store, "save_chart_trails", unavailable)
    for second, kind, payload in ((0, "combat", {"shot_cost": 2}), (1, "loot", {"value": 3, "item_name": "Animal Muscle Oil"})):
        app.store.add_event(sid, {"kind": kind, "timestamp": f"2026-01-01T10:00:0{second}",
                                  "raw_message": "trail failure", "payload": payload})
    summary = app.store.get_session(sid)
    window.update_from_session(summary)
    app.update()
    assert window._trail_error and window.kill_trail_histories == {}
    assert [p["profit"] for p in window.kill_trend_points] == pytest.approx([1])
    assert len(window.model.points) == 1
    assert (summary.hunting_cost, summary.loot_value) == pytest.approx((2, 3))
