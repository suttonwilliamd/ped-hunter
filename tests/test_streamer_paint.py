"""The canvas painter reaches the same drawing with far fewer Tk calls.

No Tk interpreter: a recording canvas and font stand in, so this runs under --no-gui.
"""
from dataclasses import replace

import pytest

from ped_hunter import streamer_render
from ped_hunter.streamer_model import project
from ped_hunter.streamer_scene import DESIGN, scene


class _Font:
    created = 0

    def __init__(self, _root, **options):
        type(self).created += 1
        self.options = options

    def measure(self, text):
        return len(text) * abs(self.options["size"]) * 0.5


class _Canvas:
    """Holds what a Tk canvas would: ordered items with coordinates and options."""

    def __init__(self):
        self.items, self.calls, self._next = {}, [], 1
        self.background = None

    def _create(self, kind, *xy, **options):
        self.calls.append("create")
        item = self._next
        self._next += 1
        self.items[item] = [kind, tuple(xy), dict(options)]
        return item

    def create_text(self, *xy, **o): return self._create("text", *xy, **o)
    def create_line(self, *xy, **o): return self._create("line", *xy, **o)
    def create_oval(self, *xy, **o): return self._create("oval", *xy, **o)
    def create_polygon(self, *xy, **o): return self._create("polygon", *xy, **o)
    def create_rectangle(self, *xy, **o): return self._create("rectangle", *xy, **o)

    def find_all(self):
        return tuple(self.items)

    def delete(self, _what):
        self.calls.append("delete")
        self.items.clear()

    def coords(self, item, *xy):
        self.calls.append("coords")
        self.items[item][1] = tuple(xy)

    def itemconfigure(self, item, **options):
        self.calls.append("itemconfigure")
        self.items[item][2].update(options)

    def configure(self, **options):
        self.background = options["background"]

    def drawing(self):
        return self.background, [(kind, xy, {k: (v.options if isinstance(v, _Font) else v) for k, v in o.items()})
                                 for kind, xy, o in self.items.values()]


@pytest.fixture
def paint(monkeypatch):
    import tkinter.font
    monkeypatch.setattr(tkinter.font, "Font", _Font)
    _Font.created = 0
    return streamer_render.paint_canvas


def _view(theme="night-signal", scale=0.5, **changes):
    model = replace(project(None, [], (), unit="USD", running=True), **changes)
    return replace(scene(model, theme, DESIGN[0], DESIGN[1], 1, "theme"), scale=scale)


def _fresh(paint, view):
    canvas = _Canvas()
    paint(canvas, view)
    return canvas.drawing()


def test_live_update_changes_items_in_place_and_matches_a_full_repaint(paint):
    canvas = _Canvas()
    first = _view()
    paint(canvas, first)
    created = len(canvas.items)
    assert created and canvas.calls.count("create") == created
    fonts = _Font.created
    assert fonts < sum(1 for item in first.items if item.kind == "text")  # shared, not one per label

    canvas.calls.clear()
    paint(canvas, first)
    assert canvas.calls == [] and _Font.created == fonts  # already on screen

    changed = _view(state="PAUSED")
    assert changed != first
    paint(canvas, changed)
    assert "delete" not in canvas.calls and "create" not in canvas.calls
    assert 0 < len(canvas.calls) < created  # only what differs
    assert canvas.drawing() == _fresh(paint, changed)

    resized = _view(state="PAUSED", scale=0.75)
    paint(canvas, resized)
    assert "create" not in canvas.calls
    assert canvas.drawing() == _fresh(paint, resized)


def test_different_structure_or_a_foreign_change_repaints_everything(paint):
    canvas = _Canvas()
    paint(canvas, _view())
    other = _view(theme="results-first")
    canvas.calls.clear()
    paint(canvas, other)
    if [item.kind for item in other.items] != [item.kind for item in _view().items]:
        assert canvas.calls[0] == "delete"
    assert canvas.drawing() == _fresh(paint, other)

    canvas.delete("all")  # something else cleared the canvas
    canvas.calls.clear()
    paint(canvas, other)
    assert canvas.calls.count("create") == len(other.items)
    assert canvas.drawing() == _fresh(paint, other)
