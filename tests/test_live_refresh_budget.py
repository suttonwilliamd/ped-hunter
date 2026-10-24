"""Live ticks must not rebuild the entire desktop on the Tk event loop."""
from unittest.mock import Mock
from types import SimpleNamespace

from ped_hunter.app import PedHunterApp


def test_secondary_refresh_is_throttled_and_coalesced():
    app = object.__new__(PedHunterApp)
    app._full_refresh_pending = False
    app.after = Mock()
    app._schedule_full_refresh()
    app._schedule_full_refresh()
    app.after.assert_called_once_with(1500, app._run_full_refresh)


def test_secondary_tick_does_not_refresh_overlay_or_all_tabs():
    app = object.__new__(PedHunterApp)
    app.running = True
    app.session_id = 'run'
    app._refresh_visible_tab = Mock()
    app._refresh_all = Mock()
    app._run_full_refresh()
    app._refresh_visible_tab.assert_called_once_with()
    app._refresh_all.assert_not_called()


def test_events_tab_refresh_updates_both_rows_and_loot_chart():
    app = object.__new__(PedHunterApp)
    app.session_id = 'run'
    notebook = SimpleNamespace(select=lambda: 'events')
    app.dashboard_tab = SimpleNamespace(master=notebook)
    app.statistics_tab = 'statistics'
    app.events_tab = 'events'
    app._refresh_events = Mock()
    app._refresh_loot_chart = Mock()
    app._refresh_visible_tab()
    app._refresh_events.assert_called_once_with('run')
    app._refresh_loot_chart.assert_called_once_with('run')


def test_live_tick_fetches_the_run_once_and_skips_history():
    app = object.__new__(PedHunterApp)
    app.session_id = 'run'
    app.store = Mock()
    app._refresh_metrics = Mock()
    app._on_session_selected = Mock()
    app.streamer_window = Mock()
    app._refresh_live_views()
    app.store.get_session.assert_called_once_with('run')
    app.store.get_current_session.assert_not_called()
    app._on_session_selected.assert_not_called()
    app.streamer_window.update_from_session.assert_called_once_with(app.store.get_session.return_value)
