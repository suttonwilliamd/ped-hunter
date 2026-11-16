"""Cockpit chart: reconciled trajectory, contribution steps and clean user-facing text."""
import json
from types import SimpleNamespace

import pytest

from ped_hunter.cockpit_chart import chart_scene, next_landmark, step_at
from ped_hunter.hunt_trajectory import active_hours, build_trajectory, log_provenance, steps

FORBIDDEN = ("~", "≈", "unknown", "not booked", "unattributed", "not tied", "est.", "unpriced", "estimate")


def point(n, loot, cost, shots=10, *, input_cost=None, setup="Rifle", t=0.0):
    return {"kill": n, "kind": "hunt", "timestamp": f"2026-11-15T10:{n % 60:02d}:00", "loot": loot, "cost": cost,
            "input_cost": cost if input_cost is None else input_cost, "shot_count": shots,
            "context_key": json.dumps([1, setup, 0.01]), "first_event_time": t, "last_event_time": t + 30}


def hunt(n=60, *, extra_cost=1.5, extra_loot=0.0):
    pts = [point(i + 1, 0.9 if i % 3 else 1.6, 1.1, shots=10 + i % 4, t=i * 60.0) for i in range(n)]
    pts[40] = point(41, 9.0, 1.2, shots=12, t=2400.0)           # standout win
    pts[7] = point(8, 0.5, None, shots=10, input_cost=0.4)      # group without attributed cost
    loot = sum(p["loot"] for p in pts) + extra_loot
    cost = sum(p["cost"] or 0 for p in pts) + 0.4 + extra_cost
    return SimpleNamespace(loot_value=loot, hunting_cost=cost), pts


def test_trajectory_reconciles_exactly_to_session_totals():
    session, pts = hunt()
    t = build_trajectory(session, pts)
    assert sum(g.loot for g in t.groups) == pytest.approx(session.loot_value, abs=1e-9)
    assert sum(g.cost for g in t.groups) == pytest.approx(session.hunting_cost, abs=1e-9)
    assert t.cumulative[-1] == t.net == session.loot_value - session.hunting_cost
    # A group without an attributed cost carries its own booked input, never a free win.
    assert t.groups[7].cost >= 0.4
    # The remaining difference follows shots fired.
    extra = [g.cost - (0.4 if g.index == 7 else 1.2 if g.index == 40 else 1.1) for g in t.groups]
    assert extra[0] / extra[1] == pytest.approx(t.groups[0].shots / t.groups[1].shots)
    assert t.provenance["own_input_used_for"] == [8] and t.provenance["cost_spread"] == pytest.approx(1.5)


def test_negative_difference_and_loot_difference_keep_values_sane():
    session, pts = hunt(extra_cost=-2.0, extra_loot=0.75)
    t = build_trajectory(session, pts)
    assert all(g.cost >= 0 for g in t.groups)
    assert t.net == pytest.approx(session.loot_value - session.hunting_cost)
    assert t.cumulative[-1] == t.net


def test_steps_keep_standouts_and_latest_alone_and_chain_to_the_net():
    session, pts = hunt(200)
    t = build_trajectory(session, pts)
    assert [t.groups[i].number for i in t.standouts][0] == 41
    bars = steps(t, 30)
    assert len(bars) <= 30
    assert any(b.first == b.last == 40 and b.standout for b in bars)
    assert bars[-1].first == bars[-1].last == len(t.groups) - 1
    for prev, nxt in zip(bars, bars[1:]):
        assert nxt.before == prev.after and nxt.first == prev.last + 1
    assert bars[0].before == 0 and bars[-1].after == t.net


def test_chart_readout_distinguishes_encounter_gain_from_hunt_net_without_warnings():
    session, pts = hunt(120)
    t = build_trajectory(session, pts)
    rest = build_trajectory(session, pts)
    view = chart_scene(rest, 1000, 360)
    win = next(i for i, b in enumerate(view.steps) if b.standout)
    focused = chart_scene(t, 1000, 360, focus=win, focused_ring=True)
    assert "Group 41" in focused.readout and "Encounter gain ▲" in focused.readout
    assert "Hunt net after" in focused.readout
    texts = [i.text for v in (view, focused) for i in v.scene.items if i.kind == "text"]
    assert "Break-even" in texts
    assert not [x for x in texts if any(word in x.lower() for word in FORBIDDEN)]
    assert view.scene.numbers["end"] == view.scene.numbers["net"] == t.net
    # Hit testing and landmark keys land on the standout bar.
    x0, x1 = view.slots[win]
    assert step_at(view, (x0 + x1) / 2) == win
    assert next_landmark(view, None, 1) == win


def test_hunt_still_negative_after_a_win_reads_as_negative():
    pts = [point(i + 1, 0.2, 1.0, t=i * 60.0) for i in range(20)] + [point(21, 6.0, 1.0, t=1300.0)]
    session = SimpleNamespace(loot_value=sum(p["loot"] for p in pts), hunting_cost=sum(p["cost"] for p in pts))
    t = build_trajectory(session, pts)
    view = chart_scene(t, 1000, 360)
    idx = next(i for i, b in enumerate(view.steps) if b.standout)
    text = chart_scene(t, 1000, 360, focus=idx).readout
    assert "Encounter gain ▲ +$0.500" in text and "Hunt net after ▼ -$1.10" in text


def test_empty_hunt_and_active_hours():
    view = chart_scene(build_trajectory(None, []), 800, 300)
    assert view.steps == () and view.readout == "No encounters yet"
    pts = [point(i + 1, 1.0, 1.0, t=i * 60.0) for i in range(10)] + [point(11, 1.0, 1.0, t=9 * 60.0 + 3600)]
    session = SimpleNamespace(loot_value=11.0, hunting_cost=11.0)
    # A one-hour pause is not active play; short gaps between encounters are.
    assert active_hours(build_trajectory(session, pts)) == pytest.approx((9 * 60 + 30 + 30) / 3600)


def test_allocation_is_logged_next_to_the_database_once(tmp_path):
    session, pts = hunt()
    t = build_trajectory(session, pts)
    db = tmp_path / "ped-hunter.sqlite3"
    log_provenance(db, "ph-1", t)
    log_provenance(db, "ph-1", t)
    lines = (tmp_path / "logs" / "accounting-estimates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["rule"] == "own-input-then-shots/1" and record["session_id"] == "ph-1"


def test_main_window_healing_text_has_no_estimate_or_unpriced_wording():
    from ped_hunter.app import PedHunterApp
    from ped_hunter.catalog import Catalog
    from ped_hunter.currency import format_money

    class Var:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    catalog = Catalog.load()
    for heal in ("None", *list(catalog.healing_tools)[:6]):
        fake = SimpleNamespace(catalog=catalog, healing_tool=Var(heal), hot_tool=Var("None"),
                               healing_detail=Var(), healing_totals=Var(),
                               _money=lambda v, decimals=2, signed=False: format_money(v, "USD", decimals=decimals))
        PedHunterApp._refresh_healing_summary(fake, SimpleNamespace(healing_cost=0.5, estimated_healing_uses=12,
                                                                    unpriced_heal_events=3))
        shown = f"{fake.healing_detail.get()} {fake.healing_totals.get()}".lower()
        assert not [w for w in FORBIDDEN if w in shown], shown
        assert "12 uses" in shown
