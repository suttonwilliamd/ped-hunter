"""Click-to-sort for ttk.Treeview tables; the chosen order survives refreshes."""
from __future__ import annotations

import re
from tkinter import ttk

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
DESCENDING, ASCENDING = " ▾", " ▴"


def number(text: object) -> float | None:
    """The first number in a displayed cell ("-$1.69", "95.09%", "1,106"); None for blanks."""
    cleaned = str(text).replace(",", "").replace("$", "").replace("−", "-")
    match = _NUMBER.search(cleaned)
    return float(match.group()) if match else None


def make_sortable(tree: ttk.Treeview, *, numeric: tuple[str, ...] = (), text: tuple[str, ...] = (),
                  default: tuple[str, bool] | None = None) -> None:
    """Headings of ``numeric`` and ``text`` columns sort on click.

    The first click sorts numbers high-to-low and text A-to-Z; a second click
    reverses. ``default`` is (column, descending). Exact sort keys for cells whose
    text is not sortable (dates) go in ``tree.sort_keys[(iid, column)]``.
    """
    tree.sort_numeric = set(numeric)
    tree.sort_titles = {column: str(tree.heading(column, "text")) for column in (*numeric, *text)}
    tree.sort_state = default
    tree.sort_keys = {}
    for column in tree.sort_titles:
        tree.heading(column, command=lambda chosen=column: sort_by(tree, chosen))
    _show_indicator(tree)


def sort_by(tree: ttk.Treeview, column: str) -> None:
    current = tree.sort_state
    descending = not current[1] if current and current[0] == column else column in tree.sort_numeric
    tree.sort_state = (column, descending)
    apply_sort(tree)


def _show_indicator(tree: ttk.Treeview) -> None:
    state = tree.sort_state
    for column, title in tree.sort_titles.items():
        mark = (DESCENDING if state[1] else ASCENDING) if state and state[0] == column else ""
        tree.heading(column, text=title + mark)


def apply_sort(tree: ttk.Treeview) -> None:
    """Re-apply the chosen order (call after refilling the table)."""
    _show_indicator(tree)
    state = getattr(tree, "sort_state", None)
    if state is None:
        return
    column, descending = state
    numeric = column in tree.sort_numeric
    present, blank = [], []
    for iid in tree.get_children():
        key = tree.sort_keys.get((iid, column))
        if key is None:
            shown = tree.set(iid, column)
            key = number(shown) if numeric else (str(shown).casefold() or None)
        (blank if key is None else present).append((key, iid))
    # Ties keep their first-column (name) order; blanks stay last either way.
    present.sort(key=lambda item: str(tree.item(item[1], "values")[0]).casefold())
    present.sort(key=lambda item: item[0], reverse=descending)
    for index, (_key, iid) in enumerate(present + blank):
        tree.move(iid, "", index)
        tags = tree.item(iid, "tags")
        if not tags or tags[0] in ("odd", "even"):
            tree.item(iid, tags=("even" if index % 2 == 0 else "odd",))
