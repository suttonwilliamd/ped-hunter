"""Exercise observed development and reprojection on the real Tk HUD canvas."""
import math
from copy import deepcopy

import pytest

from ped_hunter.app import PedHunterApp
from ped_hunter.storage import Store


@pytest.fixture
def hud(tmp_path):
    app = PedHunterApp(store=Store(tmp_path / 'trail-hud.sqlite3'))
    try:
        app.reduced_motion.set(True)
        app.currency_mode.set('PED')
        sid = app.store.start_session('hunt')
        app.session_id, app.running = sid, True
        app._open_streamer_window()
        app.update()
        yield app, app.streamer_window, sid
    finally:
        app.destroy()


def event(app, sid, kind, second=0, **payload):
    app.store.add_event(sid, {'kind': kind, 'timestamp': f'2026-01-01T10:00:{second:02d}',
                              'raw_message': 'isolated HUD trail regression', 'payload': payload})
    with app.store.connect() as conn:
        return conn.execute('SELECT MAX(id) FROM events WHERE session_id = ?', (sid,)).fetchone()[0]


def refresh(app, overlay, sid):
    overlay.update_from_session(app.store.get_session(sid))
    app.update_idletasks()
    overlay._chart_frame(1.0)


def develop_group(app, overlay, sid):
    first = event(app, sid, 'combat', shot_cost=1)
    event(app, sid, 'loot', value=2, item_name='Animal Muscle Oil')
    refresh(app, overlay, sid)
    active = event(app, sid, 'combat', second=4, shot_cost=2)
    refresh(app, overlay, sid)
    event(app, sid, 'combat', second=4, shot_cost=3)
    refresh(app, overlay, sid)
    event(app, sid, 'loot', second=4, value=0.5, item_name='Animal Muscle Oil')
    refresh(app, overlay, sid)
    event(app, sid, 'loot', second=4, value=9.5, item_name='Shrapnel')
    refresh(app, overlay, sid)
    return first, active


def projection_spy(monkeypatch, overlay):
    captured = {}
    original = overlay._draw_kill_trail_fans

    def draw(points, x_for, y_for, value_for, scales):
        captured.update(points=list(points), x_for=x_for, y_for=y_for, value_for=value_for, scales=scales)
        return original(points, x_for, y_for, value_for, scales)

    monkeypatch.setattr(overlay, '_draw_kill_trail_fans', draw)
    return captured


def tagged_series(canvas, event_id, series):
    return [item for item in canvas.find_withtag(f'trail:{event_id}')
            if f'trail:{series}' in canvas.gettags(item)]


def vertices(canvas, items):
    return [(coords[i], coords[i + 1]) for item in items
            for coords in [canvas.coords(item)] for i in range(0, len(coords), 2)]


def contains_vertex(points, x, y):
    return any(math.isclose(px, x, abs_tol=0.01) and math.isclose(py, y, abs_tol=0.01)
               for px, py in points)


def ordinary_markers(canvas, series='loot'):
    return [item for item in canvas.find_withtag(series)
            if canvas.type(item) == 'oval' and 'trail' not in canvas.gettags(item)]


def center(canvas, item):
    x0, y0, x1, y1 = canvas.coords(item)
    return (x0 + x1) / 2, (y0 + y1) / 2


def assert_projected_envelopes(overlay, projection):
    canvas = overlay.kill_chart
    for index, point in enumerate(projection['points']):
        history = overlay.kill_trail_histories.get(point['event_id'], ())
        if len(history) < 2:
            continue
        for column, series in enumerate(('cost', 'avg_cost', 'loot')):
            known = [sample[column] for sample in history if sample[column] is not None]
            if not known or math.isclose(min(known), max(known), abs_tol=1e-12):
                continue
            items = tagged_series(canvas, point['event_id'], series)
            coords = vertices(canvas, items)
            assert coords
            x = projection['x_for'](index)
            for value in (min(known), max(known)):
                assert contains_vertex(coords, x, projection['y_for'](index, value))
            # Every endpoint belongs to this group's incoming segment, not a
            # neighboring event's slot or pixel coordinates from an earlier size.
            lower_x = projection['x_for'](max(0, index - 1)) - 2
            assert all(lower_x <= px <= x + 2 for px, _ in coords)
            assert all(0 <= py <= canvas.winfo_height() for _, py in coords)


def test_live_cost_and_loot_samples_have_meaningful_final_markers_and_no_frame_samples(hud):
    app, overlay, sid = hud
    first, active = develop_group(app, overlay, sid)
    history = overlay.kill_trail_histories[active]
    assert history == ((2, 1.5, 0), (5, 3, 0), (5, 3, 0.5), (5, 3, 10))
    assert len(overlay.kill_trend_points) == 2
    assert overlay.kill_trend_points[-1]['event_id'] == active
    assert overlay.kill_trend_points[-1]['profit'] == pytest.approx(5)
    assert app.store.get_session(sid).loot_value == pytest.approx(12)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(6)
    canvas = overlay.kill_chart
    final = ordinary_markers(canvas)[-1]
    assert canvas.itemcget(final, 'fill') == '#86EFAC'
    assert canvas.find_withtag('trail:profit') and canvas.find_withtag('trail:loss')
    assert canvas.itemcget(canvas.find_withtag('trail:profit')[0], 'fill') != canvas.itemcget(canvas.find_withtag('trail:loss')[0], 'fill')
    before = dict(overlay.kill_trail_histories)
    persisted = app.store.load_chart_trails(sid, [first, active])
    for reduced in (False, True, False, True):
        app.reduced_motion.set(reduced)
        overlay.update_from_session(app.store.get_session(sid))
        for phase in (0.0, 0.25, 0.5, 1.0):
            overlay._chart_frame(phase)
    assert overlay.kill_trail_histories == before
    assert app.store.load_chart_trails(sid, [first, active]) == persisted
    # Fans remain behind all final markers, not a replacement for those markers.
    stacking = list(canvas.find_all())
    assert max(stacking.index(i) for i in canvas.find_withtag('trail')) < min(stacking.index(i) for i in ordinary_markers(canvas))


def test_actual_group_boundaries_do_not_fabricate_intermediate_groups_between_polls(hud):
    app, overlay, sid = hud
    first = event(app, sid, 'combat', shot_cost=1)
    event(app, sid, 'loot', value=0.5, item_name='Animal Muscle Oil')
    refresh(app, overlay, sid)
    event(app, sid, 'loot', value=0.25, item_name='Residue')  # Final snapshot not yet polled.
    skipped = []
    for second in (4, 8):
        skipped.append(event(app, sid, 'combat', second=second, shot_cost=2))
        event(app, sid, 'loot', second=second, value=3, item_name='Animal Muscle Oil')
    latest = event(app, sid, 'combat', second=12, shot_cost=4)
    refresh(app, overlay, sid)
    assert [p['event_id'] for p in overlay.kill_trend_points] == [first, *skipped, latest]
    assert overlay.kill_trail_histories[first] == ((1, 1, 0.5), (1, 1, 0.75))
    assert not set(skipped) & overlay.kill_trail_histories.keys()
    assert overlay.kill_trail_histories[latest] == ((4, 2.25, 0),)
    assert not overlay.kill_chart.find_withtag(f'trail:{latest}')  # One observation is not a fan.


def test_paused_and_other_session_viewing_never_seed_historical_development(hud):
    app, overlay, sid = hud
    app.running = False
    old = []
    for second in (0, 4, 8):
        old.append(event(app, sid, 'combat', second=second, shot_cost=1))
        event(app, sid, 'loot', second=second, value=2, item_name='Animal Muscle Oil')
    refresh(app, overlay, sid)
    assert not overlay.kill_trail_histories
    assert not overlay.kill_chart.find_withtag('trail')
    assert len(ordinary_markers(overlay.kill_chart)) == 3
    other = app.store.start_session('hunt')
    app.running, app.session_id = True, other
    refresh(app, overlay, sid)
    assert not overlay.kill_trail_histories
    app.session_id = sid
    refresh(app, overlay, sid)
    assert set(overlay.kill_trail_histories) == {old[-1]}
    assert len(overlay.kill_trail_histories[old[-1]]) == 1
    assert not overlay.kill_chart.find_withtag('trail')


def test_closing_hud_and_reopening_loads_traces_without_replaying_gap(hud):
    app, overlay, sid = hud
    first, active = develop_group(app, overlay, sid)
    saved = app.store.load_chart_trails(sid, [first, active])
    app._open_streamer_window()  # Actual normal toggle/close cleanup.
    intermediate = event(app, sid, 'combat', second=8, shot_cost=2)
    event(app, sid, 'loot', second=8, value=4, item_name='Animal Muscle Oil')
    latest = event(app, sid, 'combat', second=12, shot_cost=3)
    app._open_streamer_window()
    app.update()
    reopened = app.streamer_window
    refresh(app, reopened, sid)
    assert reopened.kill_trail_histories[active] == saved[active]
    assert intermediate not in reopened.kill_trail_histories
    assert reopened.kill_trail_histories[latest] == ((3, 2.75, 0),)
    assert reopened.kill_chart.find_withtag(f'trail:{active}')
    assert not reopened.kill_chart.find_withtag(f'trail:{intermediate}')


def test_native_geometry_reprojects_for_resize_and_large_adaptive_loot(hud, monkeypatch):
    app, overlay, sid = hud
    projection = projection_spy(monkeypatch, overlay)
    _, active = develop_group(app, overlay, sid)
    assert_projected_envelopes(overlay, projection)
    canvas = overlay.kill_chart
    initial_width = canvas.winfo_width()
    initial_geometry = tuple(tuple(canvas.coords(i)) for i in canvas.find_withtag(f'trail:{active}'))
    saved = dict(overlay.kill_trail_histories)
    overlay.geometry(f'{overlay.winfo_width() + 220}x{overlay.winfo_height() + 120}')
    app.update()  # Deliver native Configure before asserting the new projection.
    overlay._chart_frame(1)
    assert canvas.winfo_width() > initial_width
    assert_projected_envelopes(overlay, projection)
    resized_geometry = tuple(tuple(canvas.coords(i)) for i in canvas.find_withtag(f'trail:{active}'))
    assert resized_geometry != initial_geometry
    assert overlay.kill_trail_histories == saved
    event(app, sid, 'loot', second=4, value=10_000_000, item_name='Shrapnel')
    refresh(app, overlay, sid)
    assert_projected_envelopes(overlay, projection)
    index = len(projection['points']) - 1
    final = ordinary_markers(canvas)[-1]
    assert center(canvas, final) == pytest.approx((projection['x_for'](index), projection['y_for'](index, 10_000_010)))
    assert canvas.itemcget(final, 'fill') == '#86EFAC'
    # The event-local range expands for the huge return; old observations and
    # the final marker use that same axis without flattening ordinary events.
    assert index in projection['scales'].local_indices
    assert 0 <= center(canvas, final)[1] < projection['y_for'](index, 10)
    assert overlay.kill_trail_histories[active][-1] == (5, 3, 10_000_010)


@pytest.mark.parametrize('outliers', [
    {3: 1.11}, {3: 600}, {}, {2: 1.11, 8: 600, 13: 2.25},
    {7: 1.11, 8: 600, 9: 2.25},
])
def test_vertical_local_sections_preserve_useful_ordinary_variation_and_event_order(hud, monkeypatch, outliers):
    _, overlay, _ = hud
    projection = projection_spy(monkeypatch, overlay)
    returns = [0.01, 0.025, 0.04, 0.07, 0.015, 0.06, 0.035, 0.08,
               0.02, 0.055, 0.03, 0.065, 0.012, 0.075, 0.045, 0.025]
    data = [dict(chart_point(i + 1, i + 1, 0.05, outliers.get(i, value)), avg_cost=0.19)
            for i, value in enumerate(returns)]
    histories = {p['event_id']: ((0.01, 0.19, 0), (0.05, 0.19, p['loot'])) for p in data}
    before = deepcopy((data, histories))
    overlay.kill_trend_points, overlay.kill_trail_histories = data, histories
    overlay.geometry('720x660')
    overlay.update()
    overlay._chart_frame(1)
    canvas = overlay.kill_chart
    assert projection['scales'].local_indices == set(outliers)
    assert projection['scales'].ordinary.high == pytest.approx(0.19)
    markers = ordinary_markers(canvas)
    assert len(markers) == 16
    centers = [center(canvas, marker) for marker in markers]
    assert all(a[0] < b[0] for a, b in zip(centers, centers[1:]))
    normal_y = [position[1] for i, position in enumerate(centers) if i not in outliers]
    plot_height = abs(projection['y_for'](None, 0) - projection['y_for'](None, 0.19))
    assert max(normal_y) - min(normal_y) > plot_height * 0.30
    sections = {}
    for index in outliers:
        event_id = data[index]['event_id']
        items = canvas.find_withtag(f'outlier:{event_id}')
        rect = next(item for item in items if canvas.type(item) == 'rectangle')
        left, top, right, bottom = canvas.coords(rect)
        assert left < centers[index][0] < right
        assert bottom - top > 2 * (right - left)  # Vertical event column, not a horizontal band.
        sections[index] = left, right
        labels = [canvas.itemcget(item, 'text') for item in items if canvas.type(item) == 'text']
        assert any(str(outliers[index]) in label and 'PED' in label for label in labels)
        assert centers[index][1] == pytest.approx(projection['y_for'](index, outliers[index]))
    for item in canvas.find_withtag('series'):
        xs = canvas.coords(item)[::2]
        assert all(not any(left <= x <= right for left, right in sections.values()) for x in xs)
        assert all(not (min(xs) < left and max(xs) > right) for left, right in sections.values())
    assert_projected_envelopes(overlay, projection)
    for index in outliers:
        for affected in (index, index + 1):
            if affected < len(data):
                assert not any(canvas.type(item) == 'polygon'
                               for item in canvas.find_withtag(f"trail:{data[affected]['event_id']}"))
    assert (data, histories) == before
    assert len(canvas.find_all()) < 450


def test_local_loss_and_fan_only_extremes_keep_honest_whiskers_after_resize(hud, monkeypatch):
    _, overlay, _ = hud
    projection = projection_spy(monkeypatch, overlay)
    data = [dict(chart_point(i + 1, i + 1, 0.05, 0.02), avg_cost=0.19) for i in range(16)]
    data[7].update(cost=1.11, profit=-1.09)
    histories = {8: ((0.01, 0.19, 0), (1.11, 0.19, 0.02)),
                 16: ((0.01, 0.19, 0), (600, 0.19, 0.02), (0.05, 0.19, 0.02))}
    overlay.kill_trend_points, overlay.kill_trail_histories = data, histories
    saved = deepcopy(histories)
    for geometry in ('720x660', '960x800', '520x480'):
        overlay.geometry(geometry)
        overlay.update()
        for phase in (0, 0.5, 1):
            overlay._chart_frame(phase)
            assert projection['scales'].local_indices == {7, 15}
            assert projection['scales'].events[15].high == 600
            assert_projected_envelopes(overlay, projection)
            canvas = overlay.kill_chart
            markers = ordinary_markers(canvas)
            assert canvas.itemcget(markers[7], 'fill') == '#FDA4AF'
            assert center(canvas, markers[-1]) == pytest.approx(
                (projection['x_for'](15), projection['y_for'](15, 0.02)))
            for event_id in (8, 16):
                assert not any(canvas.type(item) == 'polygon'
                               for item in canvas.find_withtag(f'trail:{event_id}'))
    assert histories == saved



def test_shared_cost_revisions_follow_existing_fans_through_reload_resize_and_range(hud, monkeypatch):
    app, overlay, sid = hud
    for second in (0, 4, 8, 12, 16):
        for _ in range(5):
            event(app, sid, 'combat', second=second, shot_cost=0.1, loadout='Uniform carbine')
        event(app, sid, 'loot', second=second, value=0.6, item_name='Shrapnel')
        refresh(app, overlay, sid)
    first = None
    for _ in range(15):  # Three typical creatures before the first receipt.
        event_id = event(app, sid, 'combat', second=20, shot_cost=0.1, loadout='Uniform carbine')
        first = event_id if first is None else first
        refresh(app, overlay, sid)
    event(app, sid, 'loot', second=22, value=0.4, item_name='Shrapnel')
    refresh(app, overlay, sid)
    raw_before = overlay._raw_kill_trend_points
    assert raw_before[-1]['cost'] == pytest.approx(1.5)
    second_id = event(app, sid, 'loot', second=24, value=0.8, item_name='Shrapnel')
    refresh(app, overlay, sid)
    assert not overlay._attribution_error
    assert overlay.kill_trend_points.attribution_pending == pytest.approx(0.5)
    assert overlay.kill_trend_points[-2]['cost'] == pytest.approx(0.5)
    third = event(app, sid, 'loot', second=28, value=0.2, item_name='Shrapnel')
    refresh(app, overlay, sid)
    estimated = overlay.kill_trend_points[-3:]
    assert [p['event_id'] for p in estimated] == [first, second_id, third]
    assert [p['cost'] for p in estimated] == pytest.approx([0.5] * 3)
    assert overlay.kill_trend_points.attribution_pending == pytest.approx(0)
    assert raw_before[-1]['cost'] == pytest.approx(1.5)  # Old animation snapshot is immutable.
    assert app.store.get_session(sid).hunting_cost == pytest.approx(4)
    assert sum(p['cost'] for p in overlay.kill_trend_points) == pytest.approx(4)
    history = overlay.kill_trail_histories[first]
    assert max(s[0] for s in history) == pytest.approx(1.5)
    assert history[-1][0] == pytest.approx(0.5)
    assert [overlay.kill_chart.itemcget(i, 'fill') for i in ordinary_markers(overlay.kill_chart)[-3:]] == [
        '#FDA4AF', '#86EFAC', '#FDA4AF']
    assert len(overlay.kill_chart.find_withtag('estimate')) == 3
    saved = app.store.load_chart_trails(sid, [first, second_id, third])
    app._open_streamer_window()
    app._open_streamer_window()
    app.update()
    overlay = app.streamer_window
    projection = projection_spy(monkeypatch, overlay)
    refresh(app, overlay, sid)
    assert [p['cost'] for p in overlay.kill_trend_points[-3:]] == pytest.approx([0.5] * 3)
    assert app.store.load_chart_trails(sid, [first, second_id, third]) == saved
    assert_projected_envelopes(overlay, projection)
    old_width = overlay.kill_chart.winfo_width()
    overlay.geometry(f'{overlay.winfo_width() + 180}x{overlay.winfo_height() + 100}')
    app.update()
    overlay._chart_frame(1)
    assert overlay.kill_chart.winfo_width() > old_width
    assert_projected_envelopes(overlay, projection)
    for _ in range(5):
        event(app, sid, 'combat', second=32, shot_cost=0.1, loadout='Uniform carbine')
    event(app, sid, 'loot', second=34, value=10_000_000, item_name='Shrapnel')
    refresh(app, overlay, sid)
    assert not overlay._attribution_error
    assert app.store.load_chart_trails(sid, [first, second_id, third]) == saved
    assert_projected_envelopes(overlay, projection)
    final = ordinary_markers(overlay.kill_chart)[-1]
    assert center(overlay.kill_chart, final) == pytest.approx(
        (projection['x_for'](len(projection['points']) - 1), projection['y_for'](len(projection['points']) - 1, 10_000_000)))


def test_unsupported_shared_cost_has_unknown_not_free_profit_markers(hud, monkeypatch):
    app, overlay, sid = hud
    projection = projection_spy(monkeypatch, overlay)
    for _ in range(3):
        event(app, sid, 'combat', shot_cost=0.3, loadout='No baseline')
    event(app, sid, 'loot', second=2, value=0.1, item_name='Shrapnel')
    refresh(app, overlay, sid)
    for second in (4, 8):
        event(app, sid, 'loot', second=second, value=0.2, item_name='Shrapnel')
        refresh(app, overlay, sid)
    points = overlay.kill_trend_points
    assert not overlay._attribution_error
    assert all(p['cost'] is None and p['profit'] is None for p in points)
    assert points.attribution_pending == pytest.approx(0.9)
    assert app.store.get_session(sid).hunting_cost == pytest.approx(0.9)
    assert overlay.vars['cost'].get() == '0.90 PED'
    markers = ordinary_markers(overlay.kill_chart)
    assert len(markers) == 3
    assert all(overlay.kill_chart.itemcget(i, 'fill') == '#B8C5D6' for i in markers)
    assert all(overlay.kill_chart.itemcget(i, 'outline') == '#FBBF24' for i in markers)
    assert_projected_envelopes(overlay, projection)



def chart_point(event_id, number, cost, loot, kind='hunt', plan=None):
    return {'event_id': event_id, 'kill': number, 'label': f'K{number}', 'kind': kind,
            'plan_event_id': plan, 'cost': cost, 'avg_cost': cost, 'loot': loot,
            'profit': None if cost is None else loot - cost}


def test_event_mapping_visible_window_and_unknown_cost_do_not_invent_anchors(hud, monkeypatch):
    _, overlay, _ = hud
    projection = projection_spy(monkeypatch, overlay)
    points = [chart_point(100 + i, i + 1, 2, 1) for i in range(17)]
    points[-2] = chart_point(115, 16, 0, 1, kind='craft', plan=50)
    points[-1] = chart_point(116, 17, None, 3, kind='craft', plan=60)
    overlay.kill_trend_points = points
    overlay.kill_trail_histories = {
        100: ((2, 2, 0), (2, 2, 99)),  # Offscreen data must not leak into K2.
        101: ((1, 1, 0.25), (2, 2, 1)),
        115: ((0, 0, 0.25), (0, 0, 1)),
        116: ((None, None, 0.5), (None, None, 3)),
    }
    overlay._chart_frame(1)
    canvas = overlay.kill_chart
    assert not canvas.find_withtag('trail:100')
    assert_projected_envelopes(overlay, projection)
    for event_id in (101, 115, 116):
        items = canvas.find_withtag(f'trail:{event_id}')
        assert items and not any(canvas.type(i) == 'polygon' for i in items)
        index = next(i for i, p in enumerate(projection['points']) if p['event_id'] == event_id)
        x = projection['x_for'](index)
        assert all(abs(px - x) <= 2.01 for px, _ in vertices(canvas, items))
    assert not tagged_series(canvas, 116, 'cost')
    assert not tagged_series(canvas, 116, 'avg_cost')
    assert all('trail:unknown' in canvas.gettags(i) for i in tagged_series(canvas, 116, 'loot')
               if float(canvas.itemcget(i, 'width')) == 1)
    markers = ordinary_markers(canvas)
    assert canvas.itemcget(markers[-2], 'fill') == '#86EFAC'  # Known zero is not unknown.
    assert canvas.itemcget(markers[-1], 'fill') == '#B8C5D6'
    assert canvas.itemcget(markers[0], 'fill') == '#FDA4AF'
    assert center(canvas, markers[-1]) == pytest.approx((projection['x_for'](15), projection['y_for'](15, 3)))


def test_dense_history_is_bounded_visually_and_keeps_final_series(hud, monkeypatch):
    _, overlay, _ = hud
    projection = projection_spy(monkeypatch, overlay)
    overlay.kill_trend_points = [chart_point(1, 1, 2, 8), chart_point(2, 2, 5, 12)]
    overlay.kill_trail_histories = {2: tuple((2 + 3 * i / 23, 3 + 2 * i / 23, 12 * i / 23) for i in range(24))}
    overlay._chart_frame(1)
    canvas = overlay.kill_chart
    assert_projected_envelopes(overlay, projection)
    for series in ('cost', 'avg_cost', 'loot'):
        items = tagged_series(canvas, 2, series)
        assert sum(canvas.type(i) == 'polygon' for i in items) == 1
        assert 1 <= sum(canvas.type(i) == 'line' for i in items) <= 4
    assert len(ordinary_markers(canvas)) == 2
    assert center(canvas, ordinary_markers(canvas)[-1]) == pytest.approx((projection['x_for'](1), projection['y_for'](1, 12)))


def test_trail_storage_failure_does_not_hide_base_series_or_totals(hud, monkeypatch):
    app, overlay, sid = hud

    def unavailable(*_args, **_kwargs):
        raise OSError('isolated trail storage failure')

    monkeypatch.setattr(app.store, 'save_chart_trails', unavailable)
    event(app, sid, 'combat', shot_cost=2)
    event(app, sid, 'loot', value=3, item_name='Animal Muscle Oil')
    refresh(app, overlay, sid)
    assert len(overlay.kill_trend_points) == 1
    assert overlay.kill_trend_points[0]['profit'] == pytest.approx(1)
    assert len(ordinary_markers(overlay.kill_chart)) == 1
    assert overlay.vars['kills'].get() == '1'
    assert overlay.vars['loot'].get() == '3.00 PED'
    assert overlay.vars['cost'].get() == '2.00 PED'
    assert not overlay._trend_error
    assert not overlay.kill_trail_histories
