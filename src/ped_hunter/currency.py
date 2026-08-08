"""Display-only currency formatting for PED Hunter.

PED remains the canonical stored/calculated unit.  The Entropia conversion is
fixed at 10 PED = 1 USD, so USD mode is only a presentation conversion.
"""
from __future__ import annotations

PED_PER_USD = 10.0
DISPLAY_CURRENCIES = ("USD", "PED")


def format_money(value_ped: float, mode: str = "USD", *, signed: bool = False, decimals: int = 2) -> str:
    """Format a PED amount in the selected display currency."""
    normalized_mode = mode.upper() if mode else "USD"
    if normalized_mode not in DISPLAY_CURRENCIES:
        normalized_mode = "USD"
    value = float(value_ped) / PED_PER_USD if normalized_mode == "USD" else float(value_ped)
    if normalized_mode == "USD":
        if signed:
            sign = "+" if value >= 0 else "-"
            return f"{sign}${abs(value):.{decimals}f}"
        return f"${value:.{decimals}f}"
    number = f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
    return f"{number} PED"


def currency_name(mode: str = "USD") -> str:
    return "USD" if mode.upper() == "USD" else "PED"