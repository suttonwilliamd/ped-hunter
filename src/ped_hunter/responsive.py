"""Main-window size classes: the layout changes at a few thresholds, not per pixel.

Sizes are logical pixels (window pixels divided by the monitor scale). A resize
only costs arithmetic until a threshold is crossed; widgets are re-laid then.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SIZE = (960, 640)
MIN_SIZE = (520, 360)
WIDE_W = 1000       # roomy readouts and a wider rail
DOCK_W = 720        # below: the rail becomes a run bar plus a drawer
DOCK_H = 600
SPLIT_H = 520       # below (compact only): chart and tabs take turns
TIGHT_W = 600       # below: cost per hour leaves the readout strip
SLACK = 16          # a threshold must be crossed by this much to switch back

# Readout strip fonts in points: (hunt net, other values, labels, card values).
FONTS = {"wide": (20, 13, 8, 14), "regular": (16, 12, 8, 14), "compact": (13, 10, 8, 12)}
RAIL_WIDTH = {"wide": 224, "regular": 196, "compact": 232}


@dataclass(frozen=True, slots=True)
class SizeClass:
    name: str = "regular"    # wide | regular | compact
    split: bool = False      # chart and tabs are exclusive
    tight: bool = False      # narrowest readout strip

    @property
    def docked(self) -> bool:
        return self.name != "compact"


def classify(width: float, height: float, previous: SizeClass | None = None) -> SizeClass:
    """Size class for a logical window size, sticky around each threshold."""
    def at_least(value: float, threshold: float, was_at_least: bool) -> bool:
        return value >= (threshold - SLACK if was_at_least else threshold)

    was_docked = previous is not None and previous.docked
    docked = at_least(width, DOCK_W, was_docked) and at_least(height, DOCK_H, was_docked)
    if docked:
        wide = at_least(width, WIDE_W, previous is not None and previous.name == "wide")
        return SizeClass("wide" if wide else "regular")
    was_compact = previous is not None and not previous.docked
    split = not at_least(height, SPLIT_H, was_compact and not previous.split)
    tight = not at_least(width, TIGHT_W, was_compact and not previous.tight)
    return SizeClass("compact", split, tight)


def chart_height(body_height: float, size: SizeClass) -> float:
    """Logical height of the hunt chart row inside the body."""
    if size.split:
        return 0.0
    return max(190.0, min(300.0, body_height * (0.42 if size.docked else 0.5)))
