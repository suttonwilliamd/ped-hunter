"""One retained scene graph drawn by BOTH the Tk canvas and the offscreen renderer.

Themes supply geometry and ink only. Every number, caption and chart sample
comes from ``HudModel``. Coordinates are a fixed 720x660 design space (the
legacy 360x330 HUD at 2x); the host scales the whole scene uniformly, so
nothing reflows, moves or resizes when values or healing rows change.
"""
from __future__ import annotations

from dataclasses import dataclass

from .chart_scales import plan_chart_scales
from .currency import format_chart_money

THEMES = {
    "current": "Current", "results-first": "Results First", "field-ledger": "Field Ledger",
    "night-signal": "Night Signal", "inkwing": "Inkwing", "copper-ledger": "Copper Ledger",
    "vector-beacon": "Vector Beacon",
}
DESIGN = (720, 660)
MIN_SIZE = (520, 480)
DEFAULT_SIZE = DESIGN
MAX_SIZE = (1440, 1320)

_DARK_SLOTS = ("#5EEAD4", "#93C5FD", "#FCD34D", "#D8B4FE")


@dataclass(frozen=True)
class Palette:
    bg: str
    panel: str
    ink: str
    muted: str
    accent: str
    cost: str
    avg: str
    loot: str
    border: str
    active_fill: str
    slots: tuple
    verdict: str = ""        # filled hero panel; "" = theme panel
    verdict_ink: str = ""
    active_ink: str = ""     # text colour on an active tile ("" = slot colour)
    spine: str = ""
    spine_ink: str = ""
    positive: str = "#86EFAC"
    negative: str = "#FDA4AF"


PALETTES = {
    "current": Palette("#0B1220", "#131E2E", "#F1F5F9", "#B8C5D6", "#5EEAD4", "#FDBA74", "#7DD3FC",
                       "#86EFAC", "#4B5B70", "#20403F", _DARK_SLOTS, verdict="#0B1220"),
    "results-first": Palette("#0D131A", "#17212C", "#F1F5F9", "#A9B6C7", "#8EDAC8", "#F0AF79", "#AAB9ED",
                             "#8EDAC8", "#33445A", "#1C3A38", ("#8EDAC8", "#93C5FD", "#FCD34D", "#D8B4FE")),
    "field-ledger": Palette("#EEE8DA", "#E3D9C4", "#202C30", "#54615D", "#176B63", "#AA503A", "#557A99",
                            "#176B63", "#8C8676", "#CFE3DA", ("#176B63", "#2F5F9A", "#8A5A12", "#7A4FA0"),
                            verdict="#202C30", verdict_ink="#FAF5E9"),
    "night-signal": Palette("#151320", "#272238", "#F5EFF7", "#C8BED4", "#A9E7D3", "#F59589", "#BCACED",
                            "#A9E7D3", "#4A4262", "#A9E7D3", ("#A9E7D3", "#BCACED", "#F5C98B", "#F59589"),
                            active_ink="#151320", positive="#A9E7D3", negative="#F59589"),
    "inkwing": Palette("#F1EBDD", "#E6DDC9", "#1E3439", "#54635F", "#176D66", "#B55B3D", "#6B7B89",
                       "#176D66", "#8C8A7C", "#D5E6DF", ("#176D66", "#2F5F9A", "#8A5A12", "#7A4FA0"),
                       spine="#1E3439", spine_ink="#F1EBDD"),
    "copper-ledger": Palette("#F5EBDD", "#E9DDC9", "#42263D", "#715962", "#A45E3C", "#B65B36", "#766180",
                             "#32766F", "#A08A84", "#F0D9C8", ("#32766F", "#3F5F96", "#A45E3C", "#766180"),
                             verdict="#42263D", verdict_ink="#FAF5E9"),
    "vector-beacon": Palette("#100E1D", "#211A36", "#F2ECFF", "#C6BBDD", "#B9A1FF", "#F3B75D", "#C2AAFF",
                             "#6DE5DF", "#4B3F70", "#3A2D63", ("#6DE5DF", "#B9A1FF", "#F3B75D", "#F49AC1"),
                             positive="#6DE5DF", negative="#F49AC1"),
}

# Region rectangles (x, y, w, h). setup_cols: tiles per row. vertical: stats stacked.
LAYOUTS = {
    "current": dict(net=(18, 56, 232, 112), rate=(500, 56, 202, 112), setups=(256, 58, 236, 108), setup_cols=2,
                    stats=(18, 176, 684, 68), chart=(18, 280, 684, 196), activity=(18, 484, 684, 114),
                    shape="rect", hero_panel=False),
    "results-first": dict(hero=(18, 54, 684, 108), stats=(18, 172, 684, 64), chart=(18, 272, 684, 180),
                          activity=(18, 460, 684, 102), setups=(18, 568, 684, 42), setup_cols=4,
                          shape="round", line=23, size=18),
    "field-ledger": dict(net=(18, 56, 410, 112), rate=(446, 56, 256, 112), stats=(18, 180, 684, 64),
                         setups=(18, 254, 684, 44), setup_cols=4, chart=(18, 334, 684, 148),
                         activity=(18, 490, 684, 108), shape="rect", net_shape="beam", ruled=True),
    "night-signal": dict(net=(18, 54, 412, 110), rate=(442, 54, 260, 110), stats=(18, 174, 684, 62),
                         chart=(18, 272, 684, 168), setups=(18, 450, 684, 46), setup_cols=4,
                         activity=(18, 504, 684, 96), shape="round", line=23, size=17),
    "inkwing": dict(net=(14, 60, 194, 104), rate=(14, 170, 194, 90), stats=(14, 268, 194, 228), vertical=True,
                    setups=(236, 58, 468, 100), setup_cols=2, chart=(236, 194, 468, 232),
                    activity=(236, 436, 468, 162), shape="clipped", hero_panel=False, on_spine=True),
    "copper-ledger": dict(net=(18, 56, 430, 108), rate=(468, 52, 234, 116), setups=(18, 178, 684, 48), setup_cols=4,
                          stats=(18, 236, 684, 62), chart=(18, 332, 684, 150), activity=(18, 490, 684, 108),
                          shape="clipped", net_shape="clipped", rate_shape="ellipse", ruled=True),
    "vector-beacon": dict(net=(16, 56, 208, 114), rate=(16, 178, 208, 96), stats=(16, 282, 208, 198),
                          vertical=True, setups=(240, 56, 464, 100), setup_cols=2, chart=(240, 192, 464, 286),
                          activity=(16, 488, 688, 110), shape="round"),
}


@dataclass(frozen=True)
class Item:
    kind: str
    xy: tuple
    fill: str
    text: str = ""
    size: int = 20
    bold: bool = False
    outline: str = ""
    dash: tuple = ()
    width: int = 1
    target: str = ""
    budget: float = 0
    underline: bool = False
    affordance: str = ""
    anchor: str = "nw"


@dataclass(frozen=True)
class Control:
    target: str
    label: str
    role: str = "button"
    enabled: bool = True
    selected: bool = False
    focused: bool = False


@dataclass(frozen=True)
class Scene:
    theme: str
    width: int
    height: int
    scale: float
    items: tuple
    anchors: dict
    numbers: dict
    source_points: tuple
    local_indices: tuple
    controls: tuple = ()


def scene(model, theme, width=DESIGN[0], height=DESIGN[1], dpi=1, focus="theme"):
    if theme not in THEMES:
        raise ValueError("invalid theme")
    if not (dpi > 0 and width > 0 and height > 0):
        raise ValueError("invalid size")
    pal, lay = PALETTES[theme], LAYOUTS[theme]
    items, controls = [], []

    def add(kind, xy, fill, **kw):
        items.append(Item(kind, tuple(xy), fill, **kw))

    def text(x, y, value, size=18, fill=pal.ink, bold=False, budget=680, **kw):
        add("text", (x, y), fill, text=str(value), size=size, bold=bold, budget=budget, **kw)

    def box(rect, shape, fill=pal.panel, **kw):
        x, y, w, h = rect
        if shape == "clipped":
            add("polygon", (x, y, x + w - 14, y, x + w, y + 14, x + w, y + h, x + 14, y + h, x, y + h - 14), fill, **kw)
        elif shape == "beam":
            add("polygon", (x + 16, y, x + w, y, x + w - 16, y + h, x, y + h), fill, **kw)
        elif shape in ("round", "ellipse"):
            add(shape, (x, y, x + w, y + h), fill, **kw)
        else:
            add("rect", (x, y, x + w, y + h), fill, **kw)

    shape = lay["shape"]
    add("rect", (0, 0, *DESIGN), pal.bg)
    if pal.spine:
        add("rect", (0, 50, 222, 606), pal.spine)

    # Header: brand, state, theme selector, close.
    text(18, 13, "PED HUNTER", 22, pal.accent, True, budget=170)
    live = "TRACKING" in model.state
    text(190, 17, model.state, 17, pal.accent if live else pal.muted, live, budget=220)
    box((462, 9, 198, 36), "round", pal.bg, outline=pal.accent, width=2, target="theme")
    text(480, 15, THEMES[theme], 18, pal.accent, budget=136, target="theme", underline=focus == "theme")
    # Two chevrons: the pill cycles (click / arrows), it is not a dropdown.
    for x0 in (626, 638):
        add("line", (x0, 20, x0 + 6, 27), pal.accent, width=2, target="theme", affordance="theme-selector")
        add("line", (x0 + 6, 27, x0, 34), pal.accent, width=2, target="theme", affordance="theme-selector")
    controls.append(Control("theme", f"Theme: {THEMES[theme]}; click to change", focused=focus == "theme"))
    add("rect", (672, 8, 712, 46), pal.bg, target="close", affordance="close-hit-area")
    add("line", (684, 18, 700, 34), pal.ink, width=2, target="close", affordance="close")
    add("line", (700, 18, 684, 34), pal.ink, width=2, target="close", affordance="close")
    if focus == "close":
        add("line", (680, 41, 704, 41), pal.accent, width=2, affordance="close-focus")
    controls.append(Control("close", "Close HUD", focused=focus == "close"))

    net = model.get("net")
    net_ink = (pal.positive if net.value and net.value > 0 else pal.negative if net.value and net.value < 0
               else pal.verdict_ink or pal.ink)

    def metric(rect, caption, reading, fill, size, *, panel_shape=None, panel_fill=pal.panel, align="left",
               cap_ink=None, value_dy=36):
        x, y, w, h = rect
        if panel_shape:
            box(rect, panel_shape, panel_fill)
        pad = 28 if panel_shape == "beam" else 14 if panel_shape else 0
        ax = {"left": x + pad, "center": x + w / 2, "right": x + w - pad}[align]
        anchor = {"left": "nw", "center": "n", "right": "ne"}[align]
        text(ax, y + 10, caption, 18, cap_ink or pal.muted, True, budget=w - 2 * pad, anchor=anchor)
        text(ax, y + value_dy, reading.displayed, size, fill if reading.value is not None else pal.muted, True,
             budget=w - 2 * pad, anchor=anchor)

    if theme == "results-first":
        hx, hy, hw, hh = lay["hero"]
        box(lay["hero"], "round", pal.panel)
        add("line", (hx + hw / 2, hy + 18, hx + hw / 2, hy + hh - 18), pal.border, width=2)
        metric((hx, hy, hw / 2, hh), "NET", net, net_ink, 58, align="center", value_dy=40)
        metric((hx + hw / 2, hy, hw / 2, hh), "RETURN", model.get("rate"), pal.ink, 58, align="center",
               value_dy=40)
        anchors = dict(hero=lay["hero"])
    else:
        hero_panel = lay.get("hero_panel", True)
        vfill = pal.verdict or pal.panel
        on_spine = lay.get("on_spine")
        legacy = theme == "current"
        metric(lay["net"], "NET", net, net_ink, 42 if legacy else 52,
               panel_shape=(lay.get("net_shape", shape) if hero_panel else None), panel_fill=vfill,
               cap_ink=(pal.spine_ink if on_spine else pal.verdict_ink or pal.muted))
        rate_ink = pal.spine_ink if on_spine else pal.ink
        metric(lay["rate"], "RETURN", model.get("rate"), rate_ink, 34 if legacy else 46,
               panel_shape=(lay.get("rate_shape", shape) if hero_panel else None),
               align="right" if theme == "current" else "center" if lay.get("rate_shape") == "ellipse" else "left",
               cap_ink=pal.spine_ink if on_spine else None)
        anchors = dict(net=lay["net"], rate=lay["rate"])

    # Stats strip: Loot/Returns, Cost, Kills/Groups.
    sx, sy, sw, sh = lay["stats"]
    vertical = lay.get("vertical")
    captions = (("loot", "Returns" if model.crafting else "Loot"), ("cost", "Cost"),
                ("groups", "Groups" if model.crafting else "Kills"))
    if theme == "current":
        box(lay["stats"], "rect")
    for i, (key, caption) in enumerate(captions):
        if vertical:
            rect = (sx, sy + i * sh / 3, sw, sh / 3 - 10)
        else:
            gap = 0 if theme == "current" else 8
            rect = (sx + i * (sw + gap) / 3, sy, (sw + gap) / 3 - gap, sh)
        x, y, w, h = rect
        on_spine = vertical and pal.spine
        if theme != "current" and not on_spine:
            box(rect, "round" if shape == "round" else "rect")
        ink = pal.spine_ink if on_spine else pal.ink
        cap = (pal.cost if key == "cost" else pal.loot if key == "loot" and not on_spine
               else pal.spine_ink if on_spine else pal.muted)
        reading = model.get(key)
        text(x + 12, y + 6, caption, 17, cap, True, budget=w - 24)
        text(x + 12, y + 29, reading.displayed, 25, ink if reading.value is not None else pal.muted, True,
             budget=w - 24)
        if lay.get("ruled"):
            add("line", (x, y + h - 3, x + w, y + h - 3), pal.muted)

    # Setup tiles: stronger neutral outline, accent active outline + tint, slot colours.
    x, y, w, h = lay["setups"]
    cols = lay["setup_cols"]
    rows = 4 // cols
    for i in range(4):
        cw, ch = w / cols, h / rows
        cell = (x + (i % cols) * cw + 2, y + (i // cols) * ch + 2, cw - 4, ch - 4)
        setup = model.setups[i] if i < len(model.setups) else None
        enabled = bool(setup and setup.enabled)
        active = bool(setup and setup.active)
        target = f"setup:{i}" if enabled else ""
        tint = pal.slots[i] if enabled else pal.muted
        fill = pal.active_fill if active else pal.panel
        box(cell, shape if shape != "beam" else "rect", fill, outline=pal.accent if active else pal.border,
            width=3 if active else 2, dash=() if enabled else (4, 3), target=target)
        cx, cy, cwid, chei = cell
        label_ink = pal.active_ink or tint if active else tint
        ty = cy + chei / 2 - 12
        text(cx + 10, ty, str(i + 1), 18, label_ink, True, budget=18, target=target)
        name_x = cx + 32
        if active:
            add("line", (cx + 30, ty + 12, cx + 35, ty + 18), label_ink, width=2, target=target, affordance="selected-check")
            add("line", (cx + 35, ty + 18, cx + 45, ty + 5), label_ink, width=2, target=target, affordance="selected-check")
            name_x = cx + 52
        text(name_x, ty, setup.name if setup else "Empty", 18, label_ink, True,
             budget=cx + cwid - name_x - 8, target=target)
        if target and target == focus:
            box((cx - 3, cy - 3, cwid + 6, chei + 6), shape if shape != "beam" else "rect", "",
                outline=pal.ink, width=2, affordance="focus-ring")
        controls.append(Control(f"setup:{i}", f"Setup {i + 1}: " + (setup.name if setup else "Empty"),
                                enabled=enabled, selected=active, focused=bool(target and target == focus)))
    anchors["setups"] = lay["setups"]

    # Chart: real shared series with the shared ordinary/local range planner.
    cx, cy, cw, ch = lay["chart"]
    label_y = cy - 26
    text(cx, label_y, model.unit, 16, pal.muted, budget=60)
    for i, (caption, tint, dash) in enumerate((("Cost", pal.cost, ()), ("Avg", pal.avg, (6, 4)),
                                               ("Returns", pal.loot, ()))):
        kx = cx + cw - 300 + i * 100
        add("line", (kx, label_y + 10, kx + 20, label_y + 10), tint, dash=dash, width=2)
        if i == 2:
            add("ellipse", (kx + 6, label_y + 6, kx + 14, label_y + 14), tint)
        text(kx + 25, label_y, caption, 16, tint, budget=72)
    box(lay["chart"], "round" if shape == "round" else "rect")
    points = model.points
    scales = plan_chart_scales(points, model.histories)
    px, py, pw, ph = cx + 112, cy + 16, cw - 128, ch - 44
    for f in ((0, .5, 1) if points else ()):
        yy = py + ph * (1 - f)
        add("line", (px, yy, px + pw, yy), pal.border, width=1)
        value = scales.ordinary.low + f * (scales.ordinary.high - scales.ordinary.low)
        text(cx + 8, yy - 9, format_chart_money(value, model.unit), 15, pal.muted, budget=100)
    for j, p in enumerate(points):
        xx = px + pw * (j / (len(points) - 1) if len(points) > 1 else .5)
        if j in scales.local_indices:
            add("line", (xx, py, xx, py + ph), pal.muted, dash=(3, 4))
            text(min(xx - 30, cx + cw - 96), py, format_chart_money(scales.events[j].high, model.unit), 14,
                 pal.ink, budget=92)
        history = model.histories.get(p.get("event_id"), ())
        for column, tint in enumerate((pal.cost, pal.avg, pal.loot)):
            values = [sample[column] for sample in history if sample[column] is not None]
            if len(values) > 1:
                ys = [py + ph * (1 - scales.events[j].fraction(v)) for v in values]
                hx = xx + (column - 1) * 4
                add("line", (hx, min(ys), hx, max(ys)), tint, dash=(1, 3), width=1)
        for key, tint, dash in (("cost", pal.cost, ()), ("avg_cost", pal.avg, (6, 4)), ("loot", pal.loot, ())):
            v = p.get(key)
            if v is None:
                continue
            yy = py + ph * (1 - scales.events[j].fraction(v))
            if j and scales.connected(j - 1, j) and points[j - 1].get(key) is not None:
                prev_y = py + ph * (1 - scales.events[j - 1].fraction(points[j - 1][key]))
                add("line", (px + pw * ((j - 1) / (len(points) - 1)), prev_y, xx, yy), tint, dash=dash, width=2)
            if key == "loot":
                add("ellipse", (xx - 4, yy - 4, xx + 4, yy + 4), tint)
            elif key == "cost":
                add("rect", (xx - 3, yy - 3, xx + 3, yy + 3), tint)
        if j in (0, len(points) - 1):
            text(xx, py + ph + 6, str(p.get("label", j + 1)), 14, pal.muted, budget=60, anchor="n")
    if not points:
        idle = ("Chart unavailable" if model.diagnostics.get("trend_available") is False else
                "Waiting for first craft group" if model.crafting else "Waiting for first kill")
        text(cx + cw / 2, cy + ch / 2 - 12, idle, 18, pal.muted, budget=cw - 40, anchor="n")
    anchors["chart"] = lay["chart"]

    # Fixed-height activity / healing area: always four rows, never reflows.
    ax, ay, aw, ah = lay["activity"]
    line, size = lay.get("line", 27), lay.get("size", 18)
    if theme == "results-first":
        box(lay["activity"], "round")
    tx = ax + (14 if theme == "results-first" else 0)
    order = (0, 1, 2, 3) if model.crafting else (0, 1, 3, 2)
    bar_row = order.index(3)
    bar_beside = tx + 0.5 * size * len(model.activity[3]) + 14 + 120 < ax + aw
    under = 0 if bar_beside or model.durability is None else 16  # room for a rule under its own row

    def row_top(row):
        return ay + 6 + row * line + (under if row > bar_row else 0)

    for row, index in enumerate(order):
        value = model.activity[index]
        y = row_top(row)
        strong = row == 0
        text(tx, y, value, size, pal.ink if strong else pal.muted, strong, budget=aw - 28)
    if model.durability is not None:
        pct = max(0.0, min(100.0, model.durability))
        # Beside the durability text when it fits, otherwise a thin rule beneath it.
        # Starts just after the text it belongs to; the empty track stays visible.
        estimate = tx + 0.5 * size * len(model.activity[3]) + 14
        row_y = row_top(bar_row)
        if bar_beside:
            by, bx0, bx1 = row_y + size / 2 - 3, max(estimate, ax + aw - 300), ax + aw - 14
        else:
            # Too narrow to share the row: a rule directly under the durability row.
            by, bx0, bx1 = row_y + size + 8, tx, ax + aw - 14
        add("rect", (bx0, by, bx1, by + 8), pal.border, outline=pal.border)
        tint = pal.negative if pct <= 10 else pal.cost if pct <= 30 else pal.loot
        if pct > 0:
            add("rect", (bx0 + 1, by + 1, bx0 + 1 + (bx1 - bx0 - 2) * pct / 100, by + 7), tint)
    anchors["activity"] = lay["activity"]

    # Footer: deliberate, underlined, clickable context with a visible cue.
    fy = 612 if theme != "results-first" else 616
    add("line", (18, fy, 702, fy), pal.border, width=1)
    text(18, fy + 10, model.context, 19, pal.accent, budget=520, target="context",
         underline=True, affordance="context-link")
    box((556, fy + 7, 108, 32), "round", pal.panel, outline=pal.accent, width=2, target="context",
        affordance="context-button")
    text(572, fy + 12, "Details", 18, pal.accent, True, budget=64, target="context")
    add("line", (644, fy + 16, 651, fy + 23), pal.accent, width=2, target="context", affordance="context-open")
    add("line", (651, fy + 23, 644, fy + 30), pal.accent, width=2, target="context", affordance="context-open")
    if focus == "context":
        add("round", (10, fy + 3, 674, fy + 43), "", outline=pal.ink, width=2, affordance="context-focus")
    controls.append(Control("context", model.context + "; open details", focused=focus == "context"))
    add("rect", (680, 622, 714, 656), pal.bg, target="resize", affordance="resize-hit-area")
    for start in (686, 694, 702):
        add("line", (start, 650, 710, 650 - (710 - start)), pal.accent, width=2, target="resize",
            affordance="resize-grip")
    controls.append(Control("resize", "Resize HUD from lower-right corner", role="resize-handle"))
    anchors["footer"] = (18, fy, 684, 660 - fy)

    scale = min(width / DESIGN[0], height / DESIGN[1]) * dpi
    return Scene(theme, round(width * dpi), round(height * dpi), scale, tuple(items), anchors,
                 {r.key: r.displayed for r in model.readings}, tuple(dict(p) for p in model.points),
                 tuple(sorted(scales.local_indices)), tuple(controls))
