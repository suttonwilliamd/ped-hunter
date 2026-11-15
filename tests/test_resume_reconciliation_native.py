"""Lease-gated native recovery: real GUI refusal, explicit staging, same-run once-only reader."""
import time

from ped_hunter.app import PedHunterApp
from ped_hunter.cli import _single_gui_instance
from ped_hunter.resume_handoff import claim_resume_handoff
from ped_hunter.resume_reconciliation import inspect_resume_reconciliation, stage_reconciled_resume
from test_resume_handoff import GAP, OLD, FUTURE, append, encoded, handoff_run


def rows(store):
    with store.connect() as conn:
        return [tuple(row) for row in conn.execute('SELECT * FROM events ORDER BY id')]


def wait_gui(app, predicate, timeout=12):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.update()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Native recovery did not reach the explicit expected state')


def dispose(app):
    app._stop_log_worker()
    app.destroy()


def test_native_claim_refusal_then_proven_partial_recovery_preserves_uncertain_bookings(handoff_run):
    source,sid,path,plan,setup,old,errors=handoff_run
    claim_resume_handoff(source.store.db_path,plan)
    source.store.add_event(sid,{'kind':'manufacturing_offset','raw_message':'Synthetic unknown-price plan',
        'payload':{'blueprint':'Simple 1 Conductors Blueprint','attempts_total':15,
                   'cost_per_attempt':None,'total_cost':None}})
    append(path,encoded(GAP))
    source._process_lines(GAP[:2],sid,OLD[-1],handoff=plan)
    before=rows(source.store)
    with _single_gui_instance():
        app=PedHunterApp(store=source.store)
        try:
            app.chat_path.set(str(path));app.update()
            assert app._resume_session_id()==sid
            app.resume_selected_session();app.update()
            assert not app.running and errors and 'claimed' in errors[-1].lower()
            assert rows(source.store)==before
        finally:
            dispose(app)
    evidence=inspect_resume_reconciliation(source.store.db_path,path,sid)
    assert evidence.verified_source_rows==2 and evidence.preserved_bookkeeping_rows==1
    archive=stage_reconciled_resume(source.store.db_path,path,evidence,'native-private-recovery')
    assert archive.exists() and rows(source.store)==before
    errors.clear()
    with _single_gui_instance():
        app=PedHunterApp(store=source.store)
        try:
            app.chat_path.set(str(path));app.update()
            assert app._resume_session_id()==sid
            app.resume_selected_session()
            wait_gui(app,lambda: app.running and app._resume_pending is None and len(rows(source.store))==len(before)+1)
            assert app.session_id==sid and app._log_worker.is_alive() and not errors
            assert rows(source.store)[:len(before)]==before
            append(path,encoded([FUTURE]))
            wait_gui(app,lambda: len(rows(source.store))==len(before)+2)
            assert rows(source.store)[:len(before)]==before
            with source.store.connect() as conn:
                assert [r[0] for r in conn.execute('SELECT id FROM sessions WHERE ended_at IS NULL')]==[sid]
                messages=[r[0] for r in conn.execute('SELECT raw_message FROM events WHERE session_id=? ORDER BY id',(sid,))]
            assert all(messages.count(line)==1 for line in [*GAP,FUTURE])
            assert not errors
        finally:
            dispose(app)
