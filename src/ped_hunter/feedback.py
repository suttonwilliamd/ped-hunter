"""Bounded, UI-only feedback. Never changes tracking or financial records."""
from __future__ import annotations

from dataclasses import dataclass, field
import time
import tkinter as tk
from typing import Callable


@dataclass(frozen=True)
class Snapshot:
    session_id: str | None
    loot: float = 0.0
    events: int = 0
    skills: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Arrival:
    loot: float = 0.0
    skills: tuple[tuple[str, int], ...] = ()
    active: bool = False


class ArrivalTracker:
    """A new session, paused view, or history selection always establishes a baseline."""

    def __init__(self) -> None:
        self.previous: Snapshot | None = None
        self.was_live = False

    def observe(self, snapshot: Snapshot, live: bool) -> Arrival:
        previous = self.previous
        self.previous = snapshot
        animate = live and self.was_live and previous is not None and previous.session_id == snapshot.session_id
        self.was_live = live
        if not animate or not snapshot.session_id:
            return Arrival()
        return Arrival(
            loot=max(0.0, snapshot.loot - previous.loot),
            skills=tuple((name, count - previous.skills.get(name, 0)) for name, count in snapshot.skills.items()
                         if count > previous.skills.get(name, 0)),
            active=snapshot.events > previous.events,
        )


def ease_out(progress: float) -> float:
    return 1.0 - (1.0 - max(0.0, min(1.0, progress))) ** 3


class Animator:
    """One cancellable timer per effect; idle widgets have no animation loop."""

    def __init__(self, widget: tk.Misc) -> None:
        self.widget = widget
        self.jobs: dict[str, str] = {}
        self.values: dict[str, float] = {}
        self.targets: dict[str, float] = {}
        widget.bind("<Destroy>", self._destroy, add="+")

    def _destroy(self, event) -> None:
        if event.widget == self.widget:
            self.cancel_all()

    def cancel(self, key: str) -> None:
        job = self.jobs.pop(key, None)
        if job:
            self.widget.after_cancel(job)

    def cancel_all(self) -> None:
        for key in list(self.jobs):
            self.cancel(key)

    def tween(self, key: str, draw: Callable[[float], None], duration: int = 360) -> None:
        self.cancel(key)
        started = time.monotonic()

        def frame() -> None:
            self.jobs.pop(key, None)
            progress = min(1.0, (time.monotonic() - started) * 1000 / duration)
            draw(ease_out(progress))
            if progress < 1:
                self.jobs[key] = self.widget.after(25, frame)

        frame()

    def number(self, key: str, target: float, render: Callable[[float], None], animate: bool) -> None:
        if animate and self.targets.get(key) == target:
            return
        start = self.values.get(key, target)
        self.targets[key] = target
        if not animate or start == target:
            self.cancel(key)
            self.values[key] = target
            render(target)
            return

        def draw(progress: float) -> None:
            value = start + (target - start) * progress
            self.values[key] = value
            render(value)

        self.tween(key, draw)


class ActivityStrip(tk.Canvas):
    """A quiet heartbeat, bundled loot arrival, and real skill progress in two lines."""

    def __init__(self, parent, *, reduced_motion: tk.BooleanVar, money: Callable[[float], str], compact=False, **kwargs):
        super().__init__(parent, height=48 if compact else 58, bg="#0b1321", highlightthickness=0, **kwargs)
        self.reduced_motion = reduced_motion
        self.money = money
        self.compact = compact
        self.animator = Animator(self)
        self.tracker = ArrivalTracker()
        self.headline = "READY FOR YOUR NEXT RUN"
        self.detail = "Loot arrivals and skill gains appear here"
        self.phase = 1.0
        self.live = False
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    def update_snapshot(self, snapshot: Snapshot, live: bool) -> Arrival:
        previous = self.tracker.previous
        resumed = live and not self.tracker.was_live
        arrival = self.tracker.observe(snapshot, live)
        self.live = live
        if self.reduced_motion.get():
            self.animator.cancel_all()
            self.phase = 1.0
        changed_session = previous is None or previous.session_id != snapshot.session_id
        if changed_session or not live or resumed:
            self.animator.cancel("arrival")
            self.phase = 1.0
            self.headline = "TRACKING LIVE" if live else "SESSION AT REST" if snapshot.session_id else "READY FOR YOUR NEXT RUN"
            procs = sum(snapshot.skills.values())
            self.detail = f"{procs:,} skill gains this run" if procs else "Loot arrivals and skill gains appear here"
        if arrival.loot > 0 or arrival.skills:
            self.headline = f"+{self.money(arrival.loot)} LOOT" if arrival.loot > 0 else "SKILL PROGRESS"
            skills = sorted(arrival.skills, key=lambda item: item[1], reverse=True)
            self.detail = "  ·  ".join(f"{name} +{count}" for name, count in skills[:2]) if skills else "Recorded in your session · totals include all costs"
            if self.reduced_motion.get():
                self.phase = 1.0
                self._draw()
            else:
                self.animator.tween("arrival", self._arrival_frame, 1600)
        elif arrival.active and not self.reduced_motion.get():
            self.animator.tween("heartbeat", self._heartbeat_frame, 450)
        self._draw()
        return arrival

    def _arrival_frame(self, phase: float) -> None:
        self.phase = phase
        self._draw()

    def _heartbeat_frame(self, phase: float) -> None:
        self._draw(pulse=1 - phase)

    def _draw(self, pulse: float = 0.0) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 100)
        h = int(self.cget("height"))
        moving = not self.reduced_motion.get()
        glow = (1 - self.phase) if moving else 0
        bg = "#10302f" if glow > 0.15 else "#0b1321"
        self.create_rectangle(0, 0, w, h, fill=bg, outline="")
        self.create_rectangle(0, 0, 3, h, fill="#2dd4bf" if self.live else "#334155", outline="")
        radius = 3 + pulse * 2
        self.create_oval(15-radius, 16-radius, 15+radius, 16+radius, fill="#2dd4bf" if self.live else "#64748b", outline="")
        offset = (1 - self.phase) * 5 if moving else 0
        self.create_text(27, 15 + offset, text=self.headline, anchor="w", fill="#d8fffb", font=("Segoe UI", 9 if self.compact else 10, "bold"), width=w-40)
        self.create_text(14, h-13, text=self.detail, anchor="w", fill="#9aaec7", font=("Segoe UI", 8 if self.compact else 9), width=w-25)
