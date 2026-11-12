"""Reversible loot-accounting membership; original events remain immutable.

Build outside the writer transaction, then include any intervening appends and
publish metadata, estimates and corrected fan endpoints together. Prior fan
observations belong to their exact exclusion set, never to another filter.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .activity_trend import ActivityTrendBuilder
from .chart_trails import bounded_samples
from .hunt_attribution import HuntAttribution, _validate_state

if TYPE_CHECKING:
    from .storage import Store


def _exclusion_key(ids):
    return hashlib.sha256(','.join(map(str, sorted(ids))).encode('ascii')).hexdigest()


def _rows(conn, session_id, event_id, excluded, after_id=0):
    return conn.execute(
        "SELECT e.id,e.timestamp,e.kind,e.payload FROM events e "
        "WHERE e.session_id=? AND e.id>? "
        "AND e.kind IN ('combat','loot','craft','manufacturing_offset','weapon','repair') AND "
        "((e.id=? AND ?=0) OR (e.id<>? AND NOT EXISTS "
        "(SELECT 1 FROM event_exclusions x WHERE x.event_id=e.id))) ORDER BY e.id",
        (session_id, after_id, event_id, excluded, event_id),
    )


def _encode(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False)


def change_loot_exclusion(store: Store, session_id: str, event_id: int, excluded: bool) -> bool:
    if type(event_id) is not int or event_id <= 0 or type(excluded) is not bool:
        raise ValueError('A recorded loot event and an explicit exclusion state are required')
    builder = ActivityTrendBuilder()
    with store.connect() as conn:
        conn.execute('BEGIN')
        target = conn.execute(
            'SELECT kind FROM events WHERE id=? AND session_id=?', (event_id, session_id),
        ).fetchone()
        if target is None or target['kind'] != 'loot':
            raise ValueError('Select a recorded loot receipt belonging to this run')
        old_ids = {int(row[0]) for row in conn.execute(
            'SELECT x.event_id FROM event_exclusions x JOIN events e ON e.id=x.event_id WHERE e.session_id=?',
            (session_id,),
        )}
        if (event_id in old_ids) == excluded:
            return False
        revision = store._accounting_revision(conn, session_id)
        watermark = int(conn.execute(
            'SELECT COALESCE(MAX(id),0) FROM events WHERE session_id=?', (session_id,),
        ).fetchone()[0])
        saved = conn.execute('SELECT state FROM hunt_attribution_state WHERE session_id=?', (session_id,)).fetchone()
        activation = None
        if saved is not None:
            state = json.loads(saved['state'])
            _validate_state(state)
            if state.get('accounting_revision', 0) != revision:
                raise ValueError('Attribution has a different accounting revision; reconcile before editing')
            activation = state['activation_event_id']
        builder.reset(revision + 1)
        builder.extend(_rows(conn, session_id, event_id, excluded))
    raw = builder.snapshot()
    engine = HuntAttribution(store)
    state, decisions = None, {}
    points = raw
    if activation is not None:
        state, decisions, points = engine.rebuild(raw, activation)

    with store.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if store._accounting_revision(conn, session_id) != revision:
            raise ValueError('Another history edit completed; refresh and try again')
        if conn.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone() is None or conn.execute(
            "SELECT 1 FROM events WHERE id=? AND session_id=? AND kind='loot'", (event_id, session_id),
        ).fetchone() is None:
            raise ValueError('The selected run or receipt no longer exists')
        # Ingestion may append while the expensive replay is preparing. Only
        # that delta is processed under the writer lock; the source is not read.
        delta = conn.execute(
            'SELECT 1 FROM events WHERE session_id=? AND id>? LIMIT 1', (session_id, watermark),
        ).fetchone()
        if delta:
            builder.extend(_rows(conn, session_id, event_id, excluded, watermark))
            raw = builder.snapshot()
            if state is not None:
                state, decisions, points = engine.extend_rebuild(raw, state, decisions)
            else:
                points = raw
        old_key = _exclusion_key(old_ids)
        new_ids = old_ids | {event_id} if excluded else old_ids - {event_id}
        new_key = _exclusion_key(new_ids)
        conn.execute(
            'INSERT INTO chart_trail_versions(session_id,exclusion_key,event_id,samples) '
            'SELECT session_id,?,event_id,samples FROM chart_trails WHERE session_id=? '
            'ON CONFLICT(session_id,exclusion_key,event_id) DO UPDATE SET samples=excluded.samples',
            (old_key, session_id),
        )
        anchors = {int(row[0]) for row in conn.execute(
            'SELECT DISTINCT event_id FROM chart_trail_versions WHERE session_id=?', (session_id,),
        )}
        histories = {int(row['event_id']): row['samples'] for row in conn.execute(
            'SELECT event_id,samples FROM chart_trail_versions WHERE session_id=? AND exclusion_key=?',
            (session_id, new_key),
        )}
        if excluded:
            conn.execute('INSERT INTO event_exclusions(event_id,excluded_at) VALUES (?,?)',
                         (event_id, datetime.now(timezone.utc).isoformat()))
        else:
            conn.execute('DELETE FROM event_exclusions WHERE event_id=?', (event_id,))
        conn.execute(
            'INSERT INTO event_accounting(session_id,revision) VALUES (?,?) '
            'ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision', (session_id, revision + 1),
        )
        conn.execute('DELETE FROM hunt_attribution_decisions WHERE session_id=?', (session_id,))
        conn.execute('DELETE FROM hunt_attribution_state WHERE session_id=?', (session_id,))
        if state is not None:
            # A physical receipt may be the activation/watermark boundary even
            # when excluded; that boundary is provenance, not attributed input.
            state['watermark'] = max(state['watermark'], watermark)
            ids = _validate_state(state)
            store._validate_hunt_ownership(conn, session_id, ids | set(decisions))
            store._validate_hunt_accounting(state, decisions)
            conn.execute('INSERT INTO hunt_attribution_state(session_id,state) VALUES (?,?)', (session_id, _encode(state)))
            conn.executemany(
                'INSERT INTO hunt_attribution_decisions(session_id,event_id,decision) VALUES (?,?,?)',
                ((session_id, key, _encode(value)) for key, value in decisions.items()),
            )
        conn.execute('DELETE FROM chart_trails WHERE session_id=?', (session_id,))
        for point in points:
            key = point['event_id']
            if key not in anchors:
                continue  # Do not fabricate previously unobserved fan histories.
            history = bounded_samples(json.loads(histories[key])) if key in histories else ()
            endpoint = (point['cost'], point['avg_cost'], point['loot'])
            if not history or history[-1] != endpoint:
                history = bounded_samples((*history, endpoint))
            conn.execute('INSERT INTO chart_trails(session_id,event_id,samples) VALUES (?,?,?)',
                         (session_id, key, _encode(history)))
        conn.commit()
    return True
