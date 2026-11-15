"""Streamer HUD projection: one shared, pure model for every theme.

Policy ``best-estimate/3`` (documented in docs/streamer-themes.md):

* Every theme shows the SAME numbers from the SAME snapshot; themes only draw.
* Arithmetic matches the legacy HUD exactly: booked session cost plus the
  supported crafting excess estimate, added once. Nothing is written back.
* Where the legacy HUD showed ``≈``/``*`` or "unknown", the HUD now shows the
  best reasonable number. Unknown crafting groups are estimated from the mean
  evidenced excess of this session's own groups (never another session); with
  no basis they add nothing beyond the booked inputs. The assumption and its
  operands are recorded in ``diagnostics`` and logged, never on the HUD.
* A value is ``None`` only when there is no session (or no denominator for the
  return rate). That renders as an em dash, which means "nothing yet", not
  uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math

from .crafting_display import crafting_cost_display
from .currency import format_chart_money, format_money

POLICY = "best-estimate/3"


def number(value, *, signed=False):
    """Finite float (non-negative unless ``signed``), else None."""
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return None
    return value if math.isfinite(value) and (signed or value >= 0) else None


@dataclass(frozen=True)
class Setup:
    id: object
    name: str
    active: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class Reading:
    key: str
    value: float | None
    displayed: str
    classification: str  # source | booked | estimated | derived | none
    formula: str


@dataclass(frozen=True)
class HudModel:
    session: str | None
    unit: str
    state: str
    crafting: bool
    readings: tuple[Reading, ...]
    points: tuple[dict, ...]
    setups: tuple[Setup, ...]
    context: str
    activity: tuple[str, str, str, str]
    durability: float | None
    diagnostics: dict
    histories: dict = field(default_factory=dict)

    def get(self, key):
        return next(r for r in self.readings if r.key == key)


def _money(value, unit, *, signed=False, precise=False):
    if value is None:
        return "—"
    text = (format_chart_money if precise else format_money)(value, unit, signed=signed)
    if not precise and 0 < abs(value) < (0.05 if unit == "USD" else 0.005):
        # Keep tiny amounts visible instead of rounding them to $0.00.
        text = format_chart_money(value, unit, signed=signed)
    return text


def _short(value, unit, *, signed=False):
    """Compact activity amount: 2 decimals from 1 up, otherwise 3 significant digits."""
    amount = value / 10 if unit == "USD" else value
    sign = ("+" if amount > 0 else "-" if amount < 0 else "") if signed else ("-" if amount < 0 else "")
    magnitude = abs(amount)
    if magnitude >= 1 or magnitude == 0:
        body = f"{magnitude:,.2f}"
    elif magnitude >= 0.0001:
        decimals = max(2, 2 - math.floor(math.log10(magnitude)))
        body = f"{magnitude:.{decimals}f}".rstrip("0")
        body += "0" * max(0, 2 - len(body.split(".")[1]))
    else:
        body = f"{magnitude:.1e}"
    return f"{sign}${body}" if unit == "USD" else f"{sign}{body} PED"


def _rate_text(rate):
    if rate is None:
        return "—"
    text = f"{rate:.2f}%"
    return text if len(text) <= 9 else f"{rate:,.0f}%"


def _latest_group(points, mode):
    """Clean two-line summary of the most recent loot/craft group."""
    if not points:
        return "", ""
    latest = points[-1]
    label = str(latest.get("label") or f"K{latest.get('kill', len(points))}")
    loot = number(latest.get("loot")) or 0.0
    cost = number(latest.get("cost"))
    basis = "assigned"
    if cost is None:
        cost = number(latest.get("excess_input_estimate"))
        basis = "excess input estimate"
    if cost is None:
        cost = number(latest.get("avg_cost"))
        basis = "running average"
    if cost is None:
        prior = [number(p.get("cost")) for p in points[:-1] if number(p.get("cost")) is not None]
        cost = math.fsum(prior) / len(prior) if prior else None
        basis = "session mean of prior groups"
    if cost is None:
        return f"{label} · Return {_short(loot, mode)}", ""
    profit = loot - cost
    first = f"{label} · Net {_short(profit, mode, signed=True)} · Return {_short(loot, mode)}"
    avg = number(latest.get("avg_cost"))
    scope = "Batch avg" if latest.get("kind") == "craft" else "Avg"
    second = f"Cost {_short(cost, mode)}"
    if avg is not None:
        second += f" · {scope} {_short(avg, mode)}"
    delta = number(latest.get("delta_profit"), signed=True)
    if delta is not None and len(points) > 1 and basis == "assigned":
        second += " · " + ("unchanged" if math.isclose(delta, 0.0, abs_tol=1e-9) else
                           f"{'▲' if delta > 0 else '▼'} {_short(abs(delta), mode)} vs prev")
    return first, second


def project(session, points, setups=(), *, unit="USD", running=False, demo=False,
            durability=None, histories=None, trend_available=True):
    """Pure projection of an applicable saved snapshot. Never mutates inputs."""
    unit = unit if unit in ("USD", "PED") else "USD"
    sid = session.session_id if session else None
    assumptions: list[str] = []
    returns = number(session.loot_value) if session else None
    booked = number(session.hunting_cost) if session else None
    if session and returns is None:
        assumptions.append(f"loot_value {session.loot_value!r} invalid; shown as 0")
        returns = 0.0
    if session and booked is None:
        assumptions.append(f"hunting_cost {session.hunting_cost!r} invalid; shown as 0")
        booked = 0.0
    clean = [dict(p) for p in points] if session else []
    display = crafting_cost_display(booked, returns, clean) if session else None
    unknown_estimate = 0.0
    if display and display.unknown_groups:
        evidenced = [number(p.get("excess_input_estimate")) for p in clean
                     if p.get("kind") == "craft" and p.get("cost") is None
                     and number(p.get("excess_input_estimate")) is not None]
        priced = [number(p.get("cost")) for p in clean
                  if p.get("kind") == "craft" and number(p.get("cost")) is not None]
        if evidenced:
            unknown_estimate = math.fsum(evidenced) / len(evidenced) * display.unknown_groups
            assumptions.append(
                f"{display.unknown_groups} craft groups without input evidence estimated at this session's "
                f"mean evidenced excess {math.fsum(evidenced) / len(evidenced):.6f} PED each")
        elif priced:
            unknown_estimate = math.fsum(priced) / len(priced) * display.unknown_groups
            assumptions.append(
                f"{display.unknown_groups} craft groups without input evidence estimated at this session's "
                f"mean priced craft-group cost {math.fsum(priced) / len(priced):.6f} PED each")
        else:
            assumptions.append(f"{display.unknown_groups} craft groups have no input basis; "
                               "they add nothing beyond booked inputs")
    if display:
        assumptions.extend(r for r in display.incomplete_reasons)
    cost = math.fsum((booked, display.excess_estimate, unknown_estimate)) if display else None
    net = returns - cost if session else None
    rate = 100.0 * returns / cost if session and cost and cost > 0 else None
    estimated = bool(display and (display.excess_estimate or unknown_estimate or display.incomplete))
    healing = number(getattr(session, "healing_cost", None)) if session else None
    heal_uses = int(getattr(session, "estimated_healing_uses", 0) or 0) if session else 0
    unpriced_heals = int(getattr(session, "unpriced_heal_events", 0) or 0) if session else 0
    if unpriced_heals:
        assumptions.append(f"{unpriced_heals} healing observations have no applicable price; "
                           "not charged (no basis to price them)")
    pending = number(getattr(points, "healing_pending", 0.0)) or 0.0
    if pending:
        assumptions.append(f"healing {pending:.6f} PED booked but not assigned to a loot group; already in Cost")
    plan = getattr(points, "manufacturing", None) if session else None
    crafting = bool(session and (getattr(points, "has_crafting", False) or (plan and plan.active)))

    readings = (
        Reading("loot", returns, _money(returns, unit), "source", "session.loot_value"),
        Reading("cost", cost, _money(cost, unit), "estimated" if estimated else "booked",
                "booked + supported excess (+ session-mean excess for unevidenced groups), once"),
        Reading("net", net, _money(net, unit, signed=True), "derived", "loot - cost"),
        Reading("rate", rate, _rate_text(rate), "derived", "100 * loot / cost"),
        Reading("groups", float(len(clean)) if session and trend_available else None,
                str(len(clean)) if session and trend_available else "—", "derived",
                "observed loot/craft groups"),
        Reading("healing", healing, _money(healing, unit) if session else "—",
                "estimated", "session.healing_cost (selected-tool estimates already in Cost)"),
    )

    latest, detail = _latest_group(clean, unit) if session else ("", "")
    if session and not trend_available:
        latest, detail = "Chart unavailable · totals unaffected", ""
        assumptions.append("trend projection failed; group count and chart withheld, totals unchanged")
    elif not latest and session:
        latest = "Waiting for first craft group" if crafting else "Waiting for first kill"
    heal_line = ""
    if session and (healing or heal_uses):
        heal_line = f"Healing {_money(healing or 0.0, unit)}" + (
            f" · {heal_uses} use{'s' if heal_uses != 1 else ''}" if heal_uses else "")
    if plan and plan.active:
        price = number(plan.per_attempt)
        input_line = (f"TT input {_short(price, unit)} · " if price is not None else "TT input · ") + \
            f"{plan.observed}/{plan.attempts} observed"
        batch = crafting_cost_display(plan.booked_cost, plan.returns,
                                      [p for p in clean if p.get("plan_event_id") == plan.event_id],
                                      unpriced_plans=int(price is None))
        batch_line = (f"Batch  Return {_short(plan.returns, unit)} · "
                      f"Cost {_short(batch.cost or 0.0, unit)} · "
                      f"Net {_short(plan.returns - (batch.cost or 0.0), unit, signed=True)}")
        activity = (latest, input_line, batch_line, heal_line)
        context = f"Crafting: {plan.blueprint}"
        dur_pct = None
    else:
        dur_text, dur_pct = durability if durability and session else ("", None)
        activity = (latest, detail, heal_line, dur_text)
        active = next((x.name for x in setups if x.active), None)
        context = ((session.loadout_name or "Session").splitlines()[0] if session
                   else active or "No active session")
    # Approximation/uncertainty marks never reach the HUD; provenance is in diagnostics.
    activity = tuple(" ".join(str(line).replace("≈", "").replace("~", "").split()) for line in activity)
    state = ("STOPPED" if session and session.ended_at else "TRACKING" if session and running
             else "PAUSED" if session else "WAITING FOR RUN" if running else "IDLE")
    if demo:
        state = "DEMO · " + state
    for p in clean:  # chart-only fill for groups whose cost is an applicable excess estimate
        if p.get("cost") is None and number(p.get("excess_input_estimate")) is not None:
            p["cost"] = p["excess_input_estimate"]
    retained = {}
    if histories:
        from .chart_trails import bounded_samples
        recent = {p.get("event_id") for p in clean[-16:]}
        for event_id, samples in histories.items():
            if event_id in recent:
                try:
                    bounded = bounded_samples(samples)
                except (TypeError, ValueError, OverflowError):
                    continue
                if bounded:
                    retained[event_id] = bounded
    diagnostics = dict(
        policy=POLICY, session=sid, trend_available=trend_available, revision=getattr(session, "accounting_revision", None), unit=unit,
        readings={r.key: dict(value=r.value, shown=r.displayed, classification=r.classification,
                              formula=r.formula) for r in readings},
        operands=dict(booked=booked, supported_excess=display.excess_estimate if display else None,
                      unknown_groups=display.unknown_groups if display else 0,
                      unknown_group_estimate=unknown_estimate, returns=returns,
                      healing_cost=healing, healing_uses=heal_uses, unpriced_heals=unpriced_heals,
                      healing_unassigned=pending,
                      attribution_unassigned=number(getattr(points, "attribution_pending", 0.0)) or 0.0),
        assumptions=assumptions,
        source_event_high_water=getattr(points, "last_event_id", None),
        groups=[p.get("event_id") for p in clean[-16:]],
        plan=dict(blueprint=plan.blueprint, observed=plan.observed, attempts=plan.attempts,
                  per_attempt=plan.per_attempt, booked=plan.booked_cost, returns=plan.returns) if plan else None,
    )
    return HudModel(sid, unit, state, crafting, readings, tuple(clean[-16:]), tuple(setups[:4]),
                    context, tuple(activity), dur_pct, diagnostics, retained)


class Diagnostics:
    """Coalesce identical estimate records; emit only when provenance changes."""

    def __init__(self, emit):
        self.emit = emit
        self.last = None
        self.text = ""

    def update(self, model, context_text=""):
        record = json.dumps(model.diagnostics, sort_keys=True, ensure_ascii=False, default=str)
        self.text = (context_text + "\n\n" if context_text else "") + \
            "ESTIMATES (" + POLICY + ")\n" + json.dumps(model.diagnostics, indent=2, sort_keys=True,
                                                         ensure_ascii=False, default=str)
        # Points/high-water change every kill; log when the money or assumptions change.
        key = json.dumps([model.diagnostics.get(k) for k in ("session", "readings", "assumptions", "plan")],
                         sort_keys=True, default=str)
        digest = hashlib.sha256(key.encode()).hexdigest()
        if digest != self.last:
            self.last = digest
            try:
                self.emit(json.loads(record))
            except Exception:  # Logging must never disturb the HUD.
                pass


class ThemePreference:
    """One app_settings row; unknown or missing values fall back to Current."""
    KEY = "streamer.theme.v1"

    def __init__(self, store):
        self.store = store

    def load(self):
        from .streamer_scene import THEMES
        try:
            with self.store.connect() as conn:
                row = conn.execute("SELECT value FROM app_settings WHERE key=?", (self.KEY,)).fetchone()
        except Exception:
            return "current"
        return row[0] if row and row[0] in THEMES else "current"

    def save(self, theme):
        from .streamer_scene import THEMES
        if theme not in THEMES:
            raise ValueError("invalid theme")
        with self.store.connect() as conn:
            conn.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.KEY, theme))
            conn.commit()
