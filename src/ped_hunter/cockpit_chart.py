"""Unified cockpit chart: encounter contributions as a waterfall over the hunt-net
trajectory, with a bold break-even line, standout-win landmarks and a docked
readout band (details never cover the plot).

``chart_scene`` is pure and returns the same Scene/Item records the streamer HUD
uses, so one scene paints on a Tk canvas (``streamer_render.paint_canvas``) and
renders offscreen (``streamer_render.rasterize``) for checks and screenshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from .currency import format_money
from .hunt_trajectory import Trajectory, steps as make_steps
from .streamer_scene import Item, Scene


@dataclass(frozen=True)
class ChartColors:
    bg: str = "#181e25"
    panel: str = "#202831"
    grid: str = "#2b3642"
    axis: str = "#394654"
    ink: str = "#edf1f5"
    muted: str = "#a5b0bc"
    gain: str = "#4ea4ea"
    gain_soft: str = "#2c5f8c"
    loss: str = "#ef8354"
    loss_soft: str = "#8a4a33"
    standout: str = "#8fd0ff"
    glow: str = "#ffd166"
    zero: str = "#d8dee6"
    focus: str = "#ffffff"


COLORS = ChartColors()
READOUT_H = 58
LANDMARK_H = 44
AXIS_H = 50
LEFT = 104
RIGHT = 128
BIG = 10_000  # text budget: chart labels are placed, never shrunk/truncated


@dataclass(frozen=True)
class ChartView:
    scene: Scene
    steps: tuple
    slots: tuple          # (x0, x1) design units per step, for hit testing
    readout: str          # plain text of the docked readout (tests/accessibility)


def money(value_ped: float, mode: str, *, signed: bool = False, total: bool = False) -> str:
    """Hunt totals use the readout strip's 2 decimals; encounter amounts under $1 keep a third."""
    small = abs(value_ped) / (10.0 if mode == "USD" else 1.0) < 1.0
    return format_money(value_ped, mode, signed=signed, decimals=3 if small and not total else 2)


def _signed(value_ped: float, mode: str, *, total: bool = False) -> str:
    return ("▲ " if value_ped >= 0 else "▼ ") + money(value_ped, mode, signed=True, total=total)


def _clock(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _nice(span: float) -> float:
    raw = span / 4 if span > 0 else 1.0
    power = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * power >= raw:
            return m * power
    return 10 * power


def step_label(t: Trajectory, step) -> str:
    first, last = t.groups[step.first], t.groups[step.last]
    if step.first == step.last:
        return f"Group {first.number}"
    return f"Groups {first.number}–{last.number}"


def readout_text(t: Trajectory, step_list, focus, mode: str) -> tuple[str, list]:
    """Docked readout: (accessible text, [(label, value, tone)])."""
    if focus is None or not (0 <= focus < len(step_list)):
        best = max(t.groups, key=lambda g: g.gain) if t.groups else None
        cells = [("Hunt net", _signed(t.net, mode, total=True), "gain" if t.net >= 0 else "loss")]
        if t.low is not None:
            cells.append((f"Low · group {t.groups[t.low].number}", money(t.cumulative[t.low], mode, signed=True, total=True), "loss"))
        if best is not None and best.gain > 0:
            cells.append((f"Best encounter · group {best.number}", _signed(best.gain, mode), "gain"))
        cells.append(("Explore", "hover, or ← → [ ]", "muted"))
    else:
        s = step_list[focus]
        first, last = t.groups[s.first], t.groups[s.last]
        when = _clock(first.timestamp) if s.first == s.last else f"{_clock(first.timestamp)}–{_clock(last.timestamp)}"
        title = f"{step_label(t, s)} · {when} · {s.shots:,} shots"
        cells = [(title, "", "ink"),
                 ("Loot", money(s.loot, mode), "ink"),
                 ("Cost", money(s.cost, mode), "ink"),
                 ("Encounter gain" if s.first == s.last else "Gain", _signed(s.gain, mode), "gain" if s.gain >= 0 else "loss"),
                 ("Hunt net after", _signed(s.after, mode, total=True), "gain" if s.after >= 0 else "loss")]
    text = " · ".join(f"{a} {b}".strip() for a, b, _ in cells)
    return text, cells


def chart_scene(t: Trajectory, width: float, height: float, *, mode: str = "USD", scale: float = 1.0,
                focus: int | None = None, pulse: float = 0.0, colors: ChartColors = COLORS,
                focused_ring: bool = False, compact: bool = False) -> ChartView:
    """``compact`` (short charts): the landmark rows and the readout share one band;
    the readout replaces the landmark labels only while an encounter is focused."""
    c = colors
    band = READOUT_H if compact else READOUT_H + LANDMARK_H  # height above the plot
    items = []

    def add(kind, xy, fill, **kw):
        items.append(Item(kind, tuple(xy), fill, **kw))

    def text(x, y, value, fill, size=14, bold=False, anchor="nw"):
        add("text", (x, y), fill, text=value, size=size, bold=bold, budget=BIG, anchor=anchor)

    add("rect", (0, 0, width, height), c.bg, outline="")
    plot_x0, plot_x1 = LEFT, max(LEFT + 60, width - RIGHT)
    plot_y0, plot_y1 = band, max(band + 60, height - AXIS_H)

    if t.empty:
        add("rect", (8, 6, width - 8, READOUT_H - 6), c.panel, outline="")
        text(20, 18, "Encounters appear here as loot arrives", c.muted, 15)
        add("line", (plot_x0, (plot_y0 + plot_y1) / 2, plot_x1, (plot_y0 + plot_y1) / 2), c.zero, width=2)
        text(plot_x0 - 8, (plot_y0 + plot_y1) / 2 - 10, "Break-even", c.muted, 13, anchor="ne")
        return ChartView(Scene("cockpit", round(width), round(height), scale, tuple(items), {}, {}, (), ()), (), (), "No encounters yet")

    slot_count = max(8, int((plot_x1 - plot_x0) // 18))
    step_list = make_steps(t, slot_count)
    if focus is not None and not (0 <= focus < len(step_list)):
        focus = None

    lo = min(0.0, *(s.before for s in step_list), *(s.after for s in step_list))
    hi = max(0.0, *(s.before for s in step_list), *(s.after for s in step_list))
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad

    def y(v):
        return plot_y1 - (v - lo) / (hi - lo) * (plot_y1 - plot_y0)

    # Grid and y ticks (values the axis actually reaches).
    tick = _nice(hi - lo)
    v = math.ceil(lo / tick) * tick
    while v <= hi + 1e-9:
        if abs(v) > tick * 1e-6:
            add("line", (plot_x0, y(v), plot_x1, y(v)), c.grid, width=1)
            text(plot_x0 - 10, y(v) - 9, money(v, mode, signed=True), c.muted, 13, anchor="ne")
        v += tick
    # Break-even reference.
    add("line", (plot_x0 - 4, y(0), plot_x1 + 6, y(0)), c.zero, width=2)
    text(plot_x0 - 10, y(0) - 10, "Break-even", c.ink, 13, bold=True, anchor="ne")

    slot = (plot_x1 - plot_x0) / len(step_list)
    bar_w = max(3.0, min(34.0, slot * 0.72))
    slots, centres = [], []
    path = [(plot_x0, y(0))]
    for i, s in enumerate(step_list):
        x0 = plot_x0 + i * slot
        cx = x0 + slot / 2
        slots.append((x0, x0 + slot))
        centres.append(cx)
        top, bottom = sorted((y(s.before), y(s.after)))
        if bottom - top < 2:
            mid = (top + bottom) / 2
            top, bottom = mid - 1, mid + 1
        up = s.after >= s.before
        if s.standout:
            if pulse > 0:
                grow = 3 + 9 * pulse
                add("rect", (cx - bar_w / 2 - grow, top - grow, cx + bar_w / 2 + grow, bottom + grow), "",
                    outline=c.glow, width=max(1, round(3 * (1 - pulse))) or 1)
            add("rect", (cx - bar_w / 2 - 3, top - 3, cx + bar_w / 2 + 3, bottom + 3), "", outline=c.glow, width=2)
            add("rect", (cx - bar_w / 2, top, cx + bar_w / 2, bottom), c.standout, outline="")
        else:
            add("rect", (cx - bar_w / 2, top, cx + bar_w / 2, bottom), c.gain if up else c.loss, outline="")
        if i + 1 < len(step_list):  # waterfall connector at the running hunt net
            add("line", (cx + bar_w / 2, y(s.after), cx + slot - bar_w / 2, y(s.after)), c.axis, width=1)
        path.append((cx, y(s.after)))
    # Hunt trajectory through the step ends.
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        add("line", (ax, ay, bx, by), c.muted, width=1, dash=(3, 3))

    # Landmarks: standout wins labelled in their own row above the plot.
    # Landmarks: standout wins labelled in two staggered rows above the plot,
    # spread apart and joined to their bars by leader lines.
    marks = [(centres[i], _signed(s.gain, mode), s) for i, s in enumerate(step_list) if s.standout]
    show_readout = not compact or focus is not None
    if compact and show_readout:
        marks = []
    rows = [marks[0::2], marks[1::2]]
    for row, members in enumerate(rows):
        widths = [len(label) * 8.6 + 10 for _, label, _ in members]
        xs = [cx for cx, _, _ in members]
        for k in range(len(xs)):          # push right to clear the previous label
            low_edge = plot_x0 + widths[k] / 2 if k == 0 else xs[k - 1] + (widths[k - 1] + widths[k]) / 2
            xs[k] = max(xs[k], low_edge)
        for k in reversed(range(len(xs))):  # then pull back inside the right edge
            high_edge = width - 8 - widths[k] / 2 if k == len(xs) - 1 else xs[k + 1] - (widths[k] + widths[k + 1]) / 2
            xs[k] = min(xs[k], high_edge)
        ly = band - LANDMARK_H + 2 + row * 20
        for (cx, label, s), lx in zip(members, xs):
            add("line", (lx, ly + 19, cx, min(y(s.before), y(s.after)) - 5), c.glow, width=1, dash=(2, 3))
            text(lx, ly, label, c.glow, 14, bold=True, anchor="n")
    # Low point: a quiet ring; the readout names it.
    if t.low is not None:
        idx = next((i for i, s in enumerate(step_list) if s.first <= t.low <= s.last), None)
        if idx is not None:
            ly = y(t.cumulative[t.low])
            add("ellipse", (centres[idx] - 4, ly - 4, centres[idx] + 4, ly + 4), "", outline=c.muted, width=2)
    # Endpoint: the hunt net, equal to the session result.
    end_x, end_y = centres[-1] + bar_w / 2 + 6, y(t.net)
    add("ellipse", (end_x - 5, end_y - 5, end_x + 5, end_y + 5), c.ink, outline=c.bg, width=2)
    text(end_x + 12, end_y + 13, "Hunt net", c.muted, 13)
    text(end_x + 12, end_y - 11, money(t.net, mode, signed=True, total=True), c.gain if t.net >= 0 else c.loss, 18, bold=True)

    # X axis: group numbers (standouts always), setup changes as a quiet band label.
    # Priority: latest group, standouts by size, then evenly spaced others; never overlapping.
    last = len(step_list) - 1
    label_every = max(1, math.ceil(46 / slot))
    order = [last] + sorted((i for i, s in enumerate(step_list) if s.standout and i != last),
                            key=lambda i: -step_list[i].gain)
    order += [i for i in range(0, last, label_every) if i not in order]
    placed = []
    for i in order:
        s = step_list[i]
        label = str(t.groups[s.first].number)
        half = len(label) * 4.2 + 4
        if any(abs(centres[i] - x) < half + h for x, h in placed):
            continue
        placed.append((centres[i], half))
        text(centres[i], plot_y1 + 6, label, c.ink if s.standout else c.muted, 13, bold=s.standout, anchor="n")
    # Setup context: only stretches on a setup other than the hunt's main one.
    counts = {}
    for g in t.groups:
        if g.setup:
            counts[g.setup] = counts.get(g.setup, 0) + 1
    main = max(counts, key=counts.get) if counts else ""
    stretches = []
    for first, last, name in t.setups:
        if not name or name == main:
            continue
        if stretches and stretches[-1][2] == name and first - stretches[-1][1] <= 3:
            stretches[-1] = (stretches[-1][0], last, name)
        else:
            stretches.append((first, last, name))
    for first, last, name in stretches:
        i0 = next(i for i, s in enumerate(step_list) if s.last >= first)
        i1 = next(i for i, s in enumerate(step_list) if s.last >= last)
        xa, xb = slots[i0][0] + 2, slots[i1][1] - 2
        add("line", (xa, plot_y1 + 26, xb, plot_y1 + 26), c.muted, width=2)
        text((xa + xb) / 2, plot_y1 + 31, f"Setup: {name}", c.muted, 13, anchor="n")
    text(plot_x0 - 10, plot_y1 + 6, "Group", c.muted, 13, anchor="ne")

    # Docked readout band.
    readout, cells = readout_text(t, step_list, focus, mode)
    if show_readout:
        add("rect", (8, 6, width - 8, READOUT_H - 4), c.panel, outline=c.focus if focused_ring else "", width=2)
    x = 22
    tones = {"gain": c.gain, "loss": c.loss, "ink": c.ink, "muted": c.muted}
    for label, value, tone in cells if show_readout else ():
        if x + max(len(label) * 7.4, len(value) * 9.2) > width - 16 and x > 22:
            break  # never draw a cell past the band
        if value:
            text(x, 12, label, c.muted, 13)
            text(x, 28, value, tones[tone], 16, bold=tone in ("gain", "loss"))
            x += max(len(label) * 7.4, len(value) * 9.2) + 30
        else:
            text(x, 20, label, c.ink, 15, bold=True)
            x += len(label) * 8.4 + 30
    if focus is not None:
        x0, x1 = slots[focus]
        s = step_list[focus]
        top, bottom = sorted((y(s.before), y(s.after)))
        add("line", ((x0 + x1) / 2, plot_y0, (x0 + x1) / 2, plot_y1), c.axis, width=1, dash=(4, 4))
        add("rect", ((x0 + x1) / 2 - bar_w / 2 - 5, top - 5, (x0 + x1) / 2 + bar_w / 2 + 5, bottom + 5), "",
            outline=c.focus, width=2)
    scene = Scene("cockpit", round(width), round(height), scale, tuple(items), {},
                  {"net": t.net, "end": t.cumulative[-1]}, (), ())
    return ChartView(scene, step_list, tuple(slots), readout)


def step_at(view: ChartView, x: float) -> int | None:
    for i, (x0, x1) in enumerate(view.slots):
        if x0 <= x < x1:
            return i
    return None


def next_landmark(view: ChartView, current: int | None, direction: int) -> int | None:
    marks = [i for i, s in enumerate(view.steps) if s.standout]
    if not marks:
        return current
    if current is None:
        return marks[0] if direction > 0 else marks[-1]
    ahead = [i for i in marks if (i > current if direction > 0 else i < current)]
    return (ahead[0] if direction > 0 else ahead[-1]) if ahead else current
