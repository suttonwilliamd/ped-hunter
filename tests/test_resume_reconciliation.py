"""Private real files/SQLite: accounting uncertainty is not ingestion ambiguity."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from ped_hunter import resume_reconciliation as recovery
from ped_hunter.cli import _single_gui_instance
from ped_hunter.resume_handoff import claim_resume_handoff, read_resume_handoff
from test_resume_handoff import GAP, OLD, FUTURE, append, encoded, handoff_run


@pytest.fixture
def claimed_run(handoff_run):
    app, sid, path, plan, setup, originals, errors = handoff_run
    claim_resume_handoff(app.store.db_path, plan)
    yield app, sid, path, plan, setup, originals, errors


def rows(store):
    with store.connect() as conn:
        return [tuple(r) for r in conn.execute('SELECT * FROM events ORDER BY id')]


def sidecars(db):
    pending = Path(str(db) + '.resume.json')
    return pending, Path(str(pending) + '.claimed')


@pytest.mark.parametrize('accounting', ['unknown-price', 'overrun', 'mixed-estimates'])
def test_accounting_uncertainty_does_not_block_proven_resume_or_change_bookings(claimed_run, accounting):
    app, sid, path, plan, setup, original_rows, errors = claimed_run
    app.store.add_event(sid, {'kind':'manufacturing_offset','raw_message':'Retained uncertain setup',
                             'payload':{'blueprint':'Simple 1 Conductors Blueprint','attempts_total':15,
                                        'cost_per_attempt':None if accounting=='unknown-price' else .7,
                                        'total_cost':None if accounting=='unknown-price' else 10.5}})
    if accounting == 'overrun':
        craft = [f'2026-11-09 01:00:{10+i:02d} [System] [] You received Simple 1 Conductors x (1) Value: 0.7 PED'
                 for i in range(17)]
        append(path, encoded(craft))
        app._process_lines(craft, sid, OLD[-1])
    elif accounting == 'mixed-estimates':
        app.store.add_event(sid, {'kind':'manufacturing_allocation','raw_message':'Manufacturing attempt allocated',
                                 'payload':{'input_cost':None,'status':'pending'}})
    before = rows(app.store)
    pending, claimed = sidecars(app.store.db_path)
    receipt_bytes = claimed.read_bytes()
    append(path, encoded(GAP))
    evidence = recovery.inspect_resume_reconciliation(app.store.db_path, path, sid)
    assert evidence.preserved_bookkeeping_rows >= 1
    assert int(evidence.offset) < path.stat().st_size  # Never choose EOF to hide downtime.
    assert rows(app.store) == before and claimed.read_bytes() == receipt_bytes and not pending.exists()
    archive = recovery.stage_reconciled_resume(app.store.db_path, path, evidence, 'isolated-recovered')
    assert archive.read_bytes() == receipt_bytes and not claimed.exists()
    staged = read_resume_handoff(app.store.db_path, sid, path, setup)
    assert staged['reconciled_from_sha256'] == hashlib.sha256(receipt_bytes).hexdigest()
    assert staged['offset'] == evidence.offset
    app.resume_selected_session()
    assert app._log_queue.get(timeout=5) == ('parsed', 3)
    app._stop_log_worker()
    assert not errors and rows(app.store)[:len(before)] == before
    assert len(rows(app.store)) == len(before) + 3
    # Repeated Resume/reconciliation never automatically replays a claimed gap.
    with pytest.raises(ValueError, match='claimed'):
        read_resume_handoff(app.store.db_path, sid, path, setup)
    assert rows(app.store)[:len(before)] == before


def test_verified_partial_progress_restarts_only_after_latest_committed_raw_line(claimed_run):
    app, sid, path, plan, setup, old, errors = claimed_run
    append(path, encoded(GAP))
    app._process_lines(GAP, sid, OLD[-1], handoff=plan)
    before = rows(app.store)
    append(path, encoded([FUTURE]))
    evidence = recovery.inspect_resume_reconciliation(app.store.db_path, path, sid)
    assert evidence.verified_source_rows == 3
    assert int(evidence.offset) == len(encoded(OLD + GAP))
    recovery.stage_reconciled_resume(app.store.db_path, path, evidence, 'partial-progress')
    app.resume_selected_session()
    assert app._log_queue.get(timeout=5) == ('parsed', 1)
    app._stop_log_worker()
    assert rows(app.store)[:len(before)] == before
    assert len(rows(app.store)) == len(before) + 1


def test_missing_parseable_line_inside_committed_span_is_integrity_failure_not_accounting_hold(claimed_run):
    app, sid, path, plan, setup, old, errors = claimed_run
    append(path, encoded(GAP))
    app._process_lines([GAP[0], GAP[2]], sid, OLD[-1], handoff=plan)
    before, receipt = rows(app.store), sidecars(app.store.db_path)[1].read_bytes()
    with pytest.raises(ValueError, match='missing/extra/reordered'):
        recovery.inspect_resume_reconciliation(app.store.db_path, path, sid)
    assert rows(app.store) == before
    assert sidecars(app.store.db_path)[1].read_bytes() == receipt
    assert not sidecars(app.store.db_path)[0].exists()


@pytest.mark.parametrize('corruption', ['prefix', 'replacement', 'truncate'])
def test_changed_source_identity_or_prefix_preserves_claim_and_database(claimed_run, corruption):
    app, sid, path, plan, setup, old, errors = claimed_run
    if corruption == 'prefix':
        data=bytearray(path.read_bytes());data[0]=ord('9');path.write_bytes(data)
    elif corruption == 'replacement':
        copy=path.with_suffix('.replacement');copy.write_bytes(path.read_bytes());copy.replace(path)
    else:
        path.write_bytes(b'short\n')
    with pytest.raises(ValueError):
        recovery.inspect_resume_reconciliation(app.store.db_path, path, sid)
    assert rows(app.store) == old and sidecars(app.store.db_path)[1].exists()


def test_foreign_later_run_cannot_be_replayed_into_old_hunt(claimed_run):
    app, sid, path, plan, setup, old, errors = claimed_run
    other=app.store.start_session('hunt')
    app.store.add_event(other, {'kind':'chat','raw_message':'[Rookie] [Synthetic] foreign', 'payload':{}})
    with pytest.raises(ValueError, match='sole unended|another run'):
        recovery.inspect_resume_reconciliation(app.store.db_path, path, sid)
    assert sidecars(app.store.db_path)[1].exists()


def test_same_second_utf8_and_embedded_vertical_tab_follow_real_lf_reader(claimed_run):
    app, sid, path, plan, setup, old, errors = claimed_run
    lines=['2026-11-09 01:01:00 [Rookie] [Synthetic] Médecine\vstill one raw message',
           '2026-11-09 01:01:00 [System] [] You received Tailings x (1) Value: 0.01 PED']
    append(path, encoded(lines))
    app._process_lines(lines, sid, OLD[-1], handoff=plan)
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    assert evidence.verified_source_rows==2
    assert int(evidence.offset)==path.stat().st_size


def test_adjacent_duplicate_suppression_and_ignored_system_lines_match_ingestion(claimed_run):
    app,sid,path,plan,setup,old,errors=claimed_run
    lines=[GAP[0],GAP[0],'2026-11-09 01:01:00 [System] [] Unparsed system notice',GAP[1],GAP[2]]
    append(path,encoded(lines));app._process_lines(lines,sid,OLD[-1],handoff=plan)
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    assert evidence.verified_source_rows==3 and evidence.consecutive_duplicates==1
    assert evidence.ignored_source_lines==1


def test_active_gui_lock_prevents_staging_even_with_valid_readonly_evidence(claimed_run):
    app,sid,path,plan,setup,old,errors=claimed_run
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    pending,claimed=sidecars(app.store.db_path);receipt=claimed.read_bytes()
    with _single_gui_instance():
        with pytest.raises(RuntimeError,match='already running'):
            recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'cannot-race-gui')
    assert not pending.exists() and claimed.read_bytes()==receipt


@pytest.mark.parametrize('mutation',['new-row','active-setup','receipt'])
def test_stale_evidence_cannot_archive_or_stage(claimed_run,mutation):
    app,sid,path,plan,setup,old,errors=claimed_run
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    if mutation=='new-row':
        app.store.add_event(sid,{'kind':'manufacturing_offset','raw_message':'Later setup','payload':{'total_cost':1}})
    elif mutation=='active-setup':
        with app.store.connect() as conn:
            conn.execute('UPDATE loadouts SET cost_per_shot=cost_per_shot+0.1 WHERE active=1');conn.commit()
    else:
        pending,claimed=sidecars(app.store.db_path);data=json.loads(claimed.read_text());data['extra']='changed';claimed.write_text(json.dumps(data))
    before=rows(app.store);receipt=sidecars(app.store.db_path)[1].read_bytes()
    with pytest.raises(ValueError,match='evidence changed'):
        recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'stale-plan')
    assert rows(app.store)==before and sidecars(app.store.db_path)[1].read_bytes()==receipt
    assert not sidecars(app.store.db_path)[0].exists()


def test_archive_failure_retains_both_guards_without_deleting_original(claimed_run,monkeypatch):
    app,sid,path,plan,setup,old,errors=claimed_run
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    pending,claimed=sidecars(app.store.db_path);receipt=claimed.read_bytes()
    monkeypatch.setattr(Path,'rename',lambda *_a,**_k: (_ for _ in ()).throw(OSError('archive failure')))
    with pytest.raises(OSError,match='archive failure'):
        recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'failed-archive')
    assert pending.exists() and claimed.read_bytes()==receipt and rows(app.store)==old
    with pytest.raises(ValueError,match='claimed'):
        read_resume_handoff(app.store.db_path,sid,path,setup)
    with pytest.raises(ValueError,match='pending cursor'):
        recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)


def test_monitor_and_gui_share_owner_lock_before_any_store_or_run_creation(claimed_run, monkeypatch):
    from unittest.mock import Mock
    from ped_hunter import cli
    app,sid,path,plan,setup,old,errors=claimed_run
    constructor=Mock(side_effect=AssertionError('Store must not open before writer lock'))
    monkeypatch.setattr(cli,'Store',constructor)
    with _single_gui_instance():
        with pytest.raises(RuntimeError,match='already running'):
            cli._monitor(path,app.store.db_path,'hunt',True)
    constructor.assert_not_called()


def test_orphaned_verified_archive_never_allows_silent_resume_at_eof(claimed_run):
    from ped_hunter.resume_handoff import has_resume_handoff
    app,sid,path,plan,setup,old,errors=claimed_run
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    archive=recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'orphan-check')
    pending,claimed=sidecars(app.store.db_path)
    pending.unlink()  # Synthetic metadata-loss scenario, never production repair.
    assert archive.exists() and not claimed.exists() and has_resume_handoff(app.store.db_path)
    with pytest.raises(ValueError,match='archive exists without a successor'):
        read_resume_handoff(app.store.db_path,sid,path,setup)


def test_oversized_successor_is_rejected_before_any_sidecar_mutation(claimed_run):
    app,sid,path,plan,setup,old,errors=claimed_run
    line='2026-11-09 01:01:00 [Rookie] [Synthetic] '+('x'*40000)
    append(path,encoded([line]));app._process_lines([line],sid,OLD[-1],handoff=plan)
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    pending,claimed=sidecars(app.store.db_path);before=claimed.read_bytes()
    with pytest.raises(ValueError,match='exceeds the bounded Resume format'):
        recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'too-large')
    assert claimed.read_bytes()==before and not pending.exists()
    assert not list(claimed.parent.glob(claimed.name+'.verified-*'))


def test_custom_scan_scope_is_retained_for_stage_time_reproof(claimed_run):
    app,sid,path,plan,setup,old,errors=claimed_run
    gap=b'not a source event '+(b'x'*(8*1024*1024))+b'\r\n'+encoded(GAP)
    append(path,gap);app._process_lines(GAP,sid,OLD[-1],handoff=plan)
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid,max_bytes=10*1024*1024)
    assert evidence.max_scan_bytes==10*1024*1024 and evidence.verified_source_rows==3
    archive=recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'custom-scope')
    assert archive.exists()


def test_mutation_between_raw_scan_and_successor_hash_is_not_certified(claimed_run,monkeypatch):
    app,sid,path,plan,setup,old,errors=claimed_run
    append(path,encoded(GAP));app._process_lines(GAP,sid,OLD[-1],handoff=plan)
    original=recovery._boundary
    def changed_boundary(stream,offset,last_line,**kw):
        data=path.read_bytes();start=int(plan['offset'])
        replacement=data[:start]+data[start:].replace(b'You missed',b'You misseX',1)
        assert len(replacement)==len(data)
        path.write_bytes(replacement)
        return original(stream,offset,last_line,**kw)
    monkeypatch.setattr(recovery,'_boundary',changed_boundary)
    with pytest.raises(ValueError,match='changed between scan'):
        recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    assert sidecars(app.store.db_path)[1].exists() and not sidecars(app.store.db_path)[0].exists()


@pytest.mark.parametrize('scope',[0,-1,True,65*1024*1024])
def test_unbounded_or_invalid_inspection_scope_is_rejected(claimed_run,scope):
    app,sid,path,plan,setup,old,errors=claimed_run
    with pytest.raises(ValueError,match='positive bounded'):
        recovery.inspect_resume_reconciliation(app.store.db_path,path,sid,max_bytes=scope)


def command_args(app,sid,path):
    return ['reconcile-resume','--db',str(app.store.db_path),'--chat-log',str(path),'--session',sid]


def test_cli_inspect_then_explicit_stage_never_starts_a_reader(claimed_run,capsys,monkeypatch):
    from ped_hunter import cli
    from unittest.mock import Mock
    app,sid,path,plan,setup,old,errors=claimed_run
    monkeypatch.setattr(cli,'Store',Mock(side_effect=AssertionError('No Store permitted')))
    append(path,encoded(GAP));app._process_lines(GAP[:2],sid,OLD[-1],handoff=plan)
    before=rows(app.store)
    args=command_args(app,sid,path)
    assert cli.main(args)==0
    summary=json.loads(capsys.readouterr().out)
    assert summary['status']=='verified-not-staged' and summary['verified_source_rows']==2
    serialized=json.dumps(summary)
    assert str(path) not in serialized and GAP[0] not in serialized and 'checkpoint_json' not in summary
    assert cli.main(args+['--stage','--expect-proof',summary['proof_sha256'],'--handoff-id','cli-check'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='staged-not-resumed' and result['accounting_changed'] is False
    assert rows(app.store)==before
    assert read_resume_handoff(app.store.db_path,sid,path,setup)['offset']==summary['offset']
    cli.Store.assert_not_called()


@pytest.mark.parametrize('extra',[
    ['--stage'], ['--stage','--expect-proof','0'*64],
    ['--handoff-id','unconfirmed'], ['--expect-proof','0'*64],
    ['--stage','--expect-proof','0'*64,'--handoff-id','stale'],
])
def test_cli_requires_explicit_matching_confirmation_before_mutation(claimed_run,capsys,extra):
    from ped_hunter import cli
    app,sid,path,plan,setup,old,errors=claimed_run
    pending,claimed=sidecars(app.store.db_path);before=claimed.read_bytes()
    assert cli.main(command_args(app,sid,path)+extra)==2
    output=capsys.readouterr();assert not output.out and 'refused' in output.err
    assert claimed.read_bytes()==before and not pending.exists()


def test_cli_no_pending_claim_error_does_not_disclose_private_path(claimed_run,capsys):
    from ped_hunter import cli
    app,sid,path,plan,setup,old,errors=claimed_run
    sidecars(app.store.db_path)[1].unlink()
    assert cli.main(command_args(app,sid,path))==2
    output=capsys.readouterr();assert str(path) not in output.err and str(app.store.db_path) not in output.err


def test_ended_run_is_not_reopened_or_merged_by_reconciliation(claimed_run):
    app,sid,path,plan,setup,old,errors=claimed_run
    app.store.end_session(sid)
    with pytest.raises(ValueError,match='same sole unended run'):
        recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    assert sidecars(app.store.db_path)[1].exists() and not sidecars(app.store.db_path)[0].exists()


def test_fsync_failure_preserves_original_claim_as_a_blocker(claimed_run,monkeypatch):
    app,sid,path,plan,setup,old,errors=claimed_run
    evidence=recovery.inspect_resume_reconciliation(app.store.db_path,path,sid)
    pending,claimed=sidecars(app.store.db_path);before=claimed.read_bytes()
    def failed_fsync(*args):
        raise OSError('Synthetic durability failure')
    monkeypatch.setattr(recovery.os,'fsync',failed_fsync)
    with pytest.raises(OSError,match='Synthetic durability'):
        recovery.stage_reconciled_resume(app.store.db_path,path,evidence,'failed-fsync')
    assert claimed.read_bytes()==before and pending.exists()
    with pytest.raises(ValueError,match='claimed'):
        read_resume_handoff(app.store.db_path,sid,path,setup)


@pytest.mark.parametrize('bad_receipt',[[],None,{'schema':1,'last_line':123}])
def test_malformed_claim_is_a_clean_cli_refusal_not_a_traceback(claimed_run,capsys,bad_receipt):
    from ped_hunter import cli
    app,sid,path,plan,setup,old,errors=claimed_run
    pending,claimed=sidecars(app.store.db_path)
    claimed.write_text(json.dumps(bad_receipt),encoding='utf-8');before=claimed.read_bytes()
    assert cli.main(command_args(app,sid,path))==2
    output=capsys.readouterr()
    assert 'refused' in output.err and 'Traceback' not in output.err and str(path) not in output.err
    assert claimed.read_bytes()==before and not pending.exists()
