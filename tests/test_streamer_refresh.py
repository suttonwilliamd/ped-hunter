from __future__ import annotations

from ped_hunter.app import (
    STREAMER_DEFAULT_HEIGHT,
    STREAMER_DEFAULT_WIDTH,
    STREAMER_MIN_HEIGHT,
    STREAMER_MIN_WIDTH,
)


def test_streamer_window_defaults_are_smaller_than_main_dashboard() -> None:
    assert STREAMER_DEFAULT_WIDTH == 400
    assert STREAMER_DEFAULT_HEIGHT == 220
    assert STREAMER_MIN_WIDTH == 320
    assert STREAMER_MIN_HEIGHT == 190
