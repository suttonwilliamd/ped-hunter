"""Width-following form scrolling without application-wide input bindings."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ScrollPane(ttk.Frame):
    """Keep natural-height content scrollable and keyboard-focused fields visible."""

    def __init__(self, parent, *, background: str):
        super().__init__(parent, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0,
                                width=1, height=1, yscrollincrement=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.content = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._viewport_changed)
        self._tag = f"ScrollPane:{self}"
        self.bind_class(self._tag, "<MouseWheel>", self._wheel)
        self.bind_class(self._tag, "<Button-4>", lambda _event: self._scroll(-1, "units"))
        self.bind_class(self._tag, "<Button-5>", lambda _event: self._scroll(1, "units"))
        self.bind_class(self._tag, "<Prior>", lambda _event: self._scroll(-1, "pages"))
        self.bind_class(self._tag, "<Next>", lambda _event: self._scroll(1, "pages"))
        self.bind_class(self._tag, "<FocusIn>", lambda event: self.reveal(event.widget))

    def _content_changed(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox(self.window))

    def _viewport_changed(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _scroll(self, count, unit):
        self.canvas.yview_scroll(count * 20 if unit == "units" else count, unit)
        return "break"

    def _wheel(self, event):
        if not event.delta:
            return None
        count = -int(event.delta / 120)
        return self._scroll(count or (-1 if event.delta > 0 else 1), "units")

    def attach(self):
        """Call after constructing content; do not capture other tabs' scrolling."""
        def attach(widget):
            tags = widget.bindtags()
            if self._tag not in tags:
                widget.bindtags((self._tag, *tags))
            for child in widget.winfo_children():
                attach(child)
        attach(self.canvas)

    def reveal(self, widget):
        if widget is self.canvas:
            return
        self.update_idletasks()
        total = self.content.winfo_height()
        viewport = self.canvas.winfo_height()
        if total <= viewport:
            return
        top = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = self.canvas.canvasy(0)
        if top < visible_top or widget.winfo_height() >= viewport:
            target = top
        elif bottom > visible_top + viewport:
            target = bottom - viewport
        else:
            return
        self.canvas.yview_moveto(max(0, target) / total)

    def fit_text(self, text):
        """Preserve selectable read-only text; scroll the page, not a clipped box."""
        pending = None

        def fit():
            nonlocal pending
            pending = None
            if text.winfo_exists() and text.winfo_width() > 40:
                lines = text.count("1.0", "end-1c", "displaylines")
                height = (lines[0] if lines else 0) + 1
                if int(text.cget("height")) != height:
                    text.configure(height=height)

        def schedule(_event=None):
            nonlocal pending
            if pending is None:
                pending = text.after_idle(fit)

        def cancel(event):
            nonlocal pending
            if event.widget is text and pending is not None:
                text.after_cancel(pending)
                pending = None

        text.bind("<Configure>", schedule, add="+")
        text.bind("<Destroy>", cancel, add="+")
        schedule()
