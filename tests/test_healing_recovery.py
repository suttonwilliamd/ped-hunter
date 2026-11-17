"""Recovering heals the old permanent unknown-effect flag left unpriced: additive and exact."""
import json

import pytest

from ped_hunter import cli
from ped_hunter.healing_recovery import recover_sessions
from test_healing import FAP, MARKER, ingest, rows, worker

VIVO = "Vivo S10"
STUCK = "Unknown effect lifecycle or overlapping casts; no paid activation inferred."


def at(second, text):
    return f"2026-11-17 10:{second // 60:02d}:{second % 60:02d} [System] [] {text}"


# Real Vivo S10 + FAP hunt shapes from 2026-11-17, plus a replayed duplicate line.
LOG = [at(0, MARKER), at(0, "You healed yourself 13.4 points"), at(1, "You healed yourself 0.2 points"),
       at(2, "You healed yourself 13.3 points"), at(2, "You healed yourself 1.0 points"),
       at(4, "You healed yourself 1.4 points"), at(13, "You healed yourself 0.4 points"),
       at(90, "You healed yourself 23.4 points"), at(93, "You healed Synthetic Avatar with 13.6 points"),
       at(94, "You healed yourself 1.1 points"), at(150, "You healed yourself 9.3 points"),
       at(240, "You healed yourself 26.1 points")]


@pytest.fixture
def stuck_run(worker):
    """Ingest with the fixed engine, then degrade payloads exactly as the old engine stored them."""
    app, sid = worker
    app.store.set_selected_healing_tool(FAP)
    app.store.set_selected_hot_tool(VIVO)
    ingest(worker, *LOG, LOG[7])  # The repeated line is stored as a duplicate audit row.
    expected = {r["id"]: r["payload"] for r in rows(worker)}
    keep = next(r["id"] for r in rows(worker) if r["payload"].get("healed") == 13.4)  # Priced before the flag stuck.
    with app.store.connect() as conn:
        for event_id, payload in expected.items():
            old = json.loads(json.dumps(payload))
            for slot in ("heal", "hot"):  # Catalog of the time: no curated Vivo behaviour yet.
                for key in ("hot_activation", "hot_tick_interval"):
                    old["healing_selection"][slot].pop(key, None)
            if event_id != keep and old.get("healing_cost_status") in {"selected_tool_estimate", "hot_tick"}:
                for key in [k for k in old if k.startswith("healing_") and k not in
                            {"healing_action", "healing_target", "healing_selection", "healing_tool"}]:
                    old.pop(key)
                old.update({"healing_cost_status": "unresolved_activation", "healing_limit": STUCK})
            conn.execute("UPDATE events SET payload = ? WHERE id = ?", (json.dumps(old), event_id))
        conn.execute("INSERT INTO healing_inference_state(session_id, state) VALUES (?, ?) ON CONFLICT(session_id) "
                     "DO UPDATE SET state = excluded.state", (sid, json.dumps({"version": 1, "unknown_effect": True})))
        conn.commit()
    return app, sid, expected, keep


def test_recovery_restores_exactly_what_the_fixed_engine_books_and_nothing_else(stuck_run):
    app, sid, expected, keep = stuck_run
    stuck = app.store.get_session(sid)
    assert (stuck.estimated_healing_uses, stuck.healing_cost) == (1, pytest.approx(0.0133))
    preserved = {r["id"]: r["payload"] for r in rows((app, sid))}

    plan = recover_sessions(app.store.db_path, [sid], app.catalog.healing_tools, apply=False)[0]
    assert app.store.get_session(sid) == stuck  # A dry run writes nothing.
    assert (plan["recovered_uses"], plan["kept_priced"], plan["priced_disagreements"]) == (4, 1, 0)
    assert plan["by_tool"] == {VIVO: {"uses": 2, "cost": 0.0266}, FAP: {"uses": 2, "cost": 0.04}}

    recover_sessions(app.store.db_path, [sid], app.catalog.healing_tools, apply=True)
    recovered = app.store.get_session(sid)
    assert recovered.estimated_healing_uses == 5
    assert recovered.healing_cost == pytest.approx(3 * 0.0133 + 2 * 0.02)
    assert recovered.hunting_cost - stuck.hunting_cost == pytest.approx(recovered.healing_cost - stuck.healing_cost)
    assert recovered.unpriced_heal_events == 1  # 9.3: capped FAP or Vivo, genuinely ambiguous.
    after = {r["id"]: r["payload"] for r in rows((app, sid))}
    for event_id, payload in after.items():
        changed = {k: v for k, v in payload.items() if k != "healing_recovered"}
        if "healing_recovered" in payload:
            # Same outcome as live ingestion with the fixed engine, and the original selection record kept.
            assert changed | {"healing_selection": None} == expected[event_id] | {"healing_selection": None}
            assert payload["healing_selection"] == preserved[event_id]["healing_selection"]
        else:
            assert payload == preserved[event_id]  # Priced, markers, ambiguous and duplicate rows untouched.
    assert after[keep] == preserved[keep]
    assert [p["healing_cost_status"] for p in after.values()].count("duplicate") == 1

    again = recover_sessions(app.store.db_path, [sid], app.catalog.healing_tools, apply=True)[0]
    assert (again["recovered_uses"], again["reclassified_ticks"]) == (0, 0)  # Idempotent.
    ingest((app, sid), at(300, "You healed yourself 24.0 points"))  # The live run continues unblocked.
    assert app.store.get_session(sid).estimated_healing_uses == 6


def test_cli_apply_refuses_while_a_tracker_holds_the_lock_and_backs_up_first(stuck_run, tmp_path, monkeypatch, capsys):
    app, sid, _expected, _keep = stuck_run
    monkeypatch.setattr(cli, "_gui_lock_path", lambda: tmp_path / "gui.lock")
    with cli._single_gui_instance():
        assert cli.main(["recover-healing", "--db", str(app.store.db_path), "--apply"]) == 2
    assert app.store.get_session(sid).estimated_healing_uses == 1
    assert cli.main(["recover-healing", "--db", str(app.store.db_path), "--apply"]) == 0
    assert list((tmp_path / "backups").glob("ped-hunter-before-heal-recovery-*.sqlite3"))
    assert app.store.get_session(sid).estimated_healing_uses == 5
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["recovered_uses"] == 4
