"""Reconciled hunt trajectory for the cockpit chart (pure; no Tk, no DB writes).

Encounter groups rarely carry every booked input: pending healing, groups whose
weapon cost has not been attributed yet and other session inputs sit only in the
session totals. The chart must end exactly at the session result, so the
remainder is allocated across groups with one consistent rule and the
allocation is recorded in ``logs/accounting-estimates.jsonl`` next to the
database. The user-facing chart shows the reconciled values only.

Rule (``ALLOCATION_RULE``):
1. a group without an attributed cost uses its own booked input cost;
2. the remaining cost difference is spread by shots fired (equal shares when no
   shots are known); a negative difference is spread by cost so no group goes
   below zero;
3. a loot difference (rare; e.g. loot booked outside a group) is spread the same way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math

ALLOCATION_RULE = "own-input-then-shots/1"


@dataclass(frozen=True)
class Group:
    index: int          # position in the hunt, 0-based
    number: int         # displayed group number (K#)
    timestamp: str
    loot: float         # PED, reconciled
    cost: float         # PED, reconciled
    shots: int
    setup: str
    start_time: float | None
    end_time: float | None

    @property
    def gain(self) -> float:
        return self.loot - self.cost


@dataclass(frozen=True)
class Step:
    """One chart bar: a single group or a run of consecutive groups."""
    first: int          # group index range [first, last]
    last: int
    before: float       # hunt net before the step (PED)
    after: float        # hunt net after the step (PED)
    loot: float
    cost: float
    shots: int
    standout: bool = False

    @property
    def gain(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class Trajectory:
    groups: tuple
    cumulative: tuple           # hunt net after each group (PED)
    loot: float                 # equals the session totals exactly
    cost: float
    net: float
    standouts: tuple            # group indices, chronological
    low: int | None             # group index of the lowest hunt net
    crossings: tuple            # group indices where hunt net turns >= 0 from below
    setups: tuple               # (first, last, name) runs of consecutive groups
    provenance: dict = field(default_factory=dict, compare=False)

    @property
    def empty(self) -> bool:
        return not self.groups


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _setup_name(point) -> str:
    key = point.get("context_key")
    if key:
        try:
            return str(json.loads(key)[1] or "")
        except (ValueError, TypeError, IndexError):
            return ""
    return ""


def _spread(amount, weights):
    total = sum(weights)
    if not weights:
        return []
    if total <= 0:
        return [amount / len(weights)] * len(weights)
    return [amount * w / total for w in weights]


def build_trajectory(session, points, *, standout_share: float = 0.25, max_standouts: int = 8) -> Trajectory:
    """Groups from ``points`` reconciled to ``session`` totals (both in PED)."""
    raw = [p for p in (points or []) if p.get("kind", "hunt") in ("hunt", "craft")]
    loot = [_num(p.get("loot")) or 0.0 for p in raw]
    attributed = [_num(p.get("cost")) for p in raw]
    own_input = [_num(p.get("input_cost")) or 0.0 for p in raw]
    missing = [i for i, c in enumerate(attributed) if c is None]
    cost = [c if c is not None else own_input[i] for i, c in enumerate(attributed)]
    shots = [max(0, int(_num(p.get("shot_count")) or 0)) for p in raw]

    session_loot = _num(getattr(session, "loot_value", None)) if session else None
    session_cost = _num(getattr(session, "hunting_cost", None)) if session else None
    loot_total = session_loot if session_loot is not None else sum(loot)
    cost_total = session_cost if session_cost is not None else sum(cost)
    cost_rest = cost_total - sum(cost)
    loot_rest = loot_total - sum(loot)
    if raw:
        by_shots = shots if sum(shots) > 0 else [1] * len(raw)
        weights = by_shots if cost_rest >= 0 else cost
        cost = [c + a for c, a in zip(cost, _spread(cost_rest, weights))]
        weights = by_shots if loot_rest >= 0 else loot
        loot = [v + a for v, a in zip(loot, _spread(loot_rest, weights))]

    groups, cumulative, running = [], [], 0.0
    for i, p in enumerate(raw):
        g = Group(i, int(p.get("kill") or i + 1), str(p.get("timestamp") or ""), loot[i], cost[i], shots[i],
                  _setup_name(p), _num(p.get("first_event_time")), _num(p.get("last_event_time")))
        groups.append(g)
        running += g.gain
        cumulative.append(running)
    if groups:  # float drift: the endpoint is the session result, exactly
        cumulative[-1] = loot_total - cost_total

    gains = sorted(((g.gain, g.index) for g in groups if g.gain > 0), reverse=True)
    top = gains[0][0] if gains else 0.0
    standouts = tuple(sorted(i for gain, i in gains[:max_standouts] if gain >= top * standout_share))
    low = min(range(len(cumulative)), key=cumulative.__getitem__) if cumulative and min(cumulative) < 0 else None
    crossings = tuple(i for i in range(1, len(cumulative)) if cumulative[i - 1] < 0 <= cumulative[i])
    setups, start = [], 0
    for i in range(1, len(groups) + 1):
        if i == len(groups) or groups[i].setup != groups[start].setup:
            setups.append((start, i - 1, groups[start].setup))
            start = i
    provenance = {
        "rule": ALLOCATION_RULE, "groups": len(groups),
        "session_loot": loot_total, "session_cost": cost_total,
        "groups_loot_booked": round(sum(_num(p.get("loot")) or 0.0 for p in raw), 6),
        "groups_cost_attributed": round(sum(c for c in attributed if c is not None), 6),
        "own_input_used_for": [groups[i].number for i in missing],
        "cost_spread": round(cost_rest, 6), "loot_spread": round(loot_rest, 6),
        "healing_pending": _num(getattr(points, "healing_pending", None)),
        "unpriced_heals": getattr(points, "unpriced_heals", None),
    }
    return Trajectory(tuple(groups), tuple(cumulative), loot_total, cost_total, loot_total - cost_total,
                      standouts, low, crossings, tuple(setups), provenance)


def steps(trajectory: Trajectory, max_steps: int) -> tuple:
    """Bars for the chart: standout groups and the latest group stand alone;
    the rest are merged into runs of a 'nice' size so at most ``max_steps`` bars remain."""
    n = len(trajectory.groups)
    if n == 0:
        return ()
    alone = set(trajectory.standouts) | {n - 1}
    max_steps = max(len(alone) + 1, max_steps)
    size = 1
    for candidate in (1, 2, 3, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        size = candidate
        if _count(n, alone, size) <= max_steps:
            break
    out, i = [], 0
    while i < n:
        if i in alone:
            j = i
        else:
            j = i
            while j + 1 < n and j + 1 not in alone and (j + 1) // size == i // size:
                j += 1
        out.append(_step(trajectory, i, j, standout=(i == j and i in trajectory.standouts)))
        i = j + 1
    return tuple(out)


def _count(n, alone, size):
    count, i = 0, 0
    while i < n:
        j = i
        if i not in alone:
            while j + 1 < n and j + 1 not in alone and (j + 1) // size == i // size:
                j += 1
        count += 1
        i = j + 1
    return count


def _step(t, first, last, *, standout):
    before = t.cumulative[first - 1] if first else 0.0
    members = t.groups[first:last + 1]
    return Step(first, last, before, t.cumulative[last], sum(g.loot for g in members),
                sum(g.cost for g in members), sum(g.shots for g in members), standout)


def active_hours(trajectory: Trajectory, gap_seconds: float = 600.0) -> float:
    """Hours with encounters, merging pauses shorter than ``gap_seconds``."""
    spans = sorted((g.start_time, g.end_time or g.start_time) for g in trajectory.groups if g.start_time is not None)
    total, start, end = 0.0, None, None
    for a, b in spans:
        if start is None:
            start, end = a, b
        elif a - end <= gap_seconds:
            end = max(end, b)
        else:
            total += end - start
            start, end = a, b
    if start is not None:
        total += end - start
    return total / 3600.0


_logged: dict = {}


def log_provenance(db_path, session_id, trajectory: Trajectory) -> None:
    """Append the allocation behind the displayed chart when it changes."""
    if trajectory.empty or db_path is None:
        return
    record = {"session_id": session_id, **trajectory.provenance}
    key = (str(db_path), session_id)
    signature = json.dumps(record, sort_keys=True, default=str)
    if _logged.get(key) == signature:
        return
    _logged[key] = signature
    folder = db_path.parent / "logs"
    name = "ped_hunter.accounting.estimates." + hashlib.sha1(str(db_path).encode()).hexdigest()[:12]
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.propagate = False
        logger.setLevel(logging.INFO)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(folder / "accounting-estimates.jsonl", maxBytes=1_000_000,
                                          backupCount=3, encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    logger.info(signature)
