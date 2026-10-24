"""Streamer-only activity presentation; dashboard feedback remains unchanged."""
from __future__ import annotations

import tkinter.font as tkfont

from .feedback import ActivityStrip, Snapshot


class OverlayActivityStrip(ActivityStrip):
    def __init__(self, *args, **kwargs):
        self.quiet_headline = "No active run"
        self.quiet_detail = "Start or resume in PED Hunter"
        self.demo = False
        self._reset_job = None
        self._scale = 1.0
        super().__init__(*args, compact=True, **kwargs)
        self.configure(bg="#131E2E")
        self.bind("<Destroy>", self._cleanup, add="+")

    def _cleanup(self, event):
        if event.widget == self and self._reset_job is not None:
            self.after_cancel(self._reset_job)
            self._reset_job = None

    def set_context(self, state: str, demo: bool = False) -> None:
        self.demo = demo
        self.quiet_headline = {
            "TRACKING": "Waiting for new events",
            "PAUSED": "Tracking paused · totals held",
            "STOPPED": "Run ended · totals held",
            "IDLE": "No active run",
            "WAITING FOR RUN": "Waiting for an active run",
        }[state]
        self.quiet_detail = ("Synthetic events · not real money" if demo else
                             "Loot and skill gains appear here" if state == "TRACKING" else
                             "Start or resume in PED Hunter" if state in ("IDLE", "WAITING FOR RUN") else
                             "Resume or start a run in PED Hunter")

    def _quiet(self):
        self._reset_job = None
        self.headline, self.detail = self.quiet_headline, self.quiet_detail
        self._draw()

    def update_snapshot(self, snapshot: Snapshot, live: bool):
        previous = self.tracker.previous
        was_live = self.tracker.was_live
        arrival = super().update_snapshot(snapshot, live)
        baseline = previous is None or previous.session_id != snapshot.session_id or not live or not was_live
        if baseline or arrival.loot > 0 or arrival.skills:
            if self._reset_job is not None:
                self.after_cancel(self._reset_job)
                self._reset_job = None
            if baseline:
                self._quiet()
            else:
                if arrival.loot > 0:
                    self.headline = f"+{self.money(arrival.loot)} loot recorded"
                if self.demo:
                    self.detail = "Synthetic events · not real money"
                self._reset_job = self.after(4000, self._quiet)
                self._draw()
        return arrival

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        if hasattr(self, '_text_font'):
            self._text_font.configure(size=-max(6, round(10 * scale)))
            self._bold_font.configure(size=-max(6, round(10 * scale)))
        self._draw()

    def _draw(self, pulse: float = 0.0):
        self.delete("all")
        width = self.winfo_width()
        if width <= 1:
            width = max(100, int(self.cget("width")))
        if not hasattr(self, "_text_font"):
            self._text_font = tkfont.Font(self, family="Segoe UI", size=-max(6, round(10 * self._scale)))
            self._bold_font = tkfont.Font(self, family="Segoe UI", size=-max(6, round(10 * self._scale)), weight="bold")
        font, bold = self._text_font, self._bold_font
        inset = max(5, round(8 * self._scale))
        gap = max(1, round(2 * self._scale))
        padding = max(2, round(3 * self._scale))
        usable = max(1, width - inset * 2)
        # Reserve exactly two single-line slots. Loot/skill arrivals and their
        # quiet reset must not change this canvas's height or move the HUD.
        height = bold.metrics("linespace") + gap + font.metrics("linespace") + 2 * padding
        if int(self.cget("height")) != height:
            self.configure(height=height)

        def elide(text, text_font):
            text = " ".join(text.split())
            if text_font.measure(text) <= usable:
                return text
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if text_font.measure(text[:middle] + "…") <= usable:
                    low = middle
                else:
                    high = middle - 1
            return text[:low] + "…"

        self.create_rectangle(0, 0, 3, height, fill="#5EEAD4" if self.live else "#334155", outline="")
        self.create_text(inset, padding, anchor="nw", text=elide(self.headline, bold),
                         font=bold, fill="#F1F5F9")
        self.create_text(inset, padding + bold.metrics("linespace") + gap, anchor="nw",
                         text=elide(self.detail, font), font=font, fill="#B8C5D6")
