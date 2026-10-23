from __future__ import annotations

import tkinter as tk
import time

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.feedback import Animator, ArrivalTracker, Snapshot, ease_out
from ped_hunter.storage import Store


def test_arrivals_baseline_history_pause_and_session_switch():
    tracker = ArrivalTracker()
    assert not tracker.observe(Snapshot("a", 100, 50, {"Rifle": 8}), True).active
    arrival = tracker.observe(Snapshot("a", 100.4, 53, {"Rifle": 10}), True)
    assert arrival.loot == pytest.approx(0.4)
    assert arrival.skills == (("Rifle", 2),)
    assert arrival.active
    assert tracker.observe(Snapshot("a", 100.4, 53, {"Rifle": 10}), True).loot == 0
    assert not tracker.observe(Snapshot("a", 105, 60), False).active
    assert tracker.observe(Snapshot("a", 110, 65), True).loot == 0
    assert tracker.observe(Snapshot("b", 500, 200), True).loot == 0
    assert tracker.observe(Snapshot(None), True).loot == 0


def test_corrections_do_not_create_negative_loot_or_skill_rewards():
    tracker = ArrivalTracker()
    tracker.observe(Snapshot("a", 10, 4, {"Rifle": 4}), True)
    arrival = tracker.observe(Snapshot("a", 9, 3, {"Rifle": 3}), True)
    assert arrival.loot == 0
    assert arrival.skills == ()
    assert not arrival.active


@pytest.mark.parametrize("progress, expected", [(-1, 0), (0, 0), (0.5, 0.875), (1, 1), (2, 1)])
def test_easing_is_bounded(progress, expected):
    assert ease_out(progress) == expected


def test_animator_replaces_jobs_settles_and_cleans_up():
    root = tk.Tk()
    animator = Animator(root)
    values = []
    try:
        animator.number("loot", 0, values.append, False)
        animator.number("loot", 10, values.append, True)
        assert len(animator.jobs) == 1
        animator.number("loot", 10, values.append, True)
        assert len(animator.jobs) == 1
        animator.number("loot", 12, values.append, True)
        assert len(animator.jobs) == 1
        animator.number("loot", 12, values.append, False)
        assert not animator.jobs
        assert values[-1] == 12
        animator.tween("short", values.append, duration=1)
        time.sleep(0.03)
        root.update()
        assert values[-1] == 1
        assert not animator.jobs
        animator.tween("pending", values.append)
    finally:
        root.destroy()
    assert not animator.jobs


def test_live_feedback_and_reduced_motion_do_not_change_records(tmp_path, monkeypatch):
    store = Store(tmp_path / "feedback.sqlite3")
    sid = store.start_session("hunt")
    app = PedHunterApp(store=store)
    try:
        app.session_id = sid
        app.running = True
        assert not app.feedback_sounds.get()
        cues = []
        monkeypatch.setattr(app.cue_player, "play", cues.append)
        app.feedback_sounds.set(True)
        app._refresh_all()
        app._open_streamer_window()
        overlay = app.streamer_window
        for kind, payload in (("loot", {"value": 4, "item_name": "Shrapnel"}), ("skill", {"skill": "Rifle", "xp": 0.01})):
            store.add_event(sid, {"kind": kind, "payload": payload, "raw_message": "test", "timestamp": "2026-01-01T10:00:00"})
        app._refresh_all()
        assert cues == ["loot"]  # The overlay does not duplicate the dashboard's cue.
        assert "+$0.40 LOOT" == app.activity_strip.headline
        assert "Rifle +1" in app.activity_strip.detail
        assert overlay.activity_strip.headline == app.activity_strip.headline
        assert any("arrival" in app.skills_tree.item(iid, "tags") for iid in app.skills_tree.get_children())
        assert any("arrival" in app.events_tree.item(iid, "tags") for iid in app.events_tree.get_children())
        app.reduced_motion.set(True)
        app._refresh_all()
        assert app.hero_loot.get() == "$0.40"
        assert overlay.vars["loot"].get() == "$0.40"
        assert not app.activity_strip.animator.jobs
        assert not overlay.activity_strip.animator.jobs
        assert app._loot_chart_phase == 1
        assert overlay._chart_phase == 1
        assert cues == ["loot"]
        before = store.get_session(sid)
        app._refresh_all()
        after = store.get_session(sid)
        assert (before.events, before.loot_value) == (after.events, after.loot_value)
        app.running = False
        app._refresh_all()
        assert app.activity_strip.headline == "SESSION AT REST"
        app.running = True
        app._refresh_all()
        assert app.activity_strip.headline == "TRACKING LIVE"
    finally:
        if app.streamer_window:
            app.streamer_window.destroy()
        app.destroy()


def test_opening_historical_loot_does_not_celebrate(tmp_path):
    store = Store(tmp_path / "history.sqlite3")
    sid = store.start_session("hunt")
    store.add_event(sid, {"kind": "loot", "payload": {"value": 100}, "raw_message": "old loot"})
    app = PedHunterApp(store=store)
    try:
        app._refresh_all()
        assert app.activity_strip.headline == "READY FOR YOUR NEXT RUN"
        assert app.hero_net.get() == "—"
        assert not app.activity_strip.animator.jobs
        assert not any("arrival" in app.events_tree.item(iid, "tags") for iid in app.events_tree.get_children())
    finally:
        app.destroy()
