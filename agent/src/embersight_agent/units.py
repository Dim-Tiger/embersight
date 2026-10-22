"""Unit and wording helpers shared by the agents.

Internally the agents keep wind in mph with direction in the meteorological
FROM convention (degrees clockwise from north that the wind blows FROM).
US fire operations talk about wind as "15 mph from the ESE", so every
narrative an IC reads goes through ``wind_phrase``.
"""

from __future__ import annotations

import math
from typing import Any

MS_TO_MPH = 2.2369362921
ACRES_TO_KM2 = 0.00404686

_CARDINALS_16 = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def ms_to_mph(speed_ms: float) -> float:
    return float(speed_ms) * MS_TO_MPH


def cardinal_16(deg: float) -> str:
    """16-point compass label for a bearing (0 = N, 90 = E)."""
    idx = int(((float(deg) % 360.0) + 11.25) // 22.5) % 16
    return _CARDINALS_16[idx]


def wind_phrase(speed_mph: Any, from_deg: Any) -> str:
    """'15 mph from ESE (101°)'. Tolerates missing parts."""
    try:
        speed = f"{float(speed_mph):.0f} mph"
    except (TypeError, ValueError):
        speed = "unknown speed"
    try:
        d = float(from_deg)
        if not math.isfinite(d):
            raise ValueError
        direction = f"from {cardinal_16(d)} ({d % 360:.0f}°)"
    except (TypeError, ValueError):
        direction = "from an unknown direction"
    return f"{speed} {direction}"


def containment_fraction(incident: Any) -> float | None:
    """Containment as a 0..1 fraction, or None when unknown.

    ``Incident.contained_pct`` arrives in two scales: the web proxy divides
    the feed's PercentContained by 100 (so 1.0 means 100 %), while older
    fixtures pass percent (10.0 means 10 %). Values above 1 are therefore
    read as percent. The one ambiguous value, exactly 1, is read as a
    fraction (fully contained) because that is what the live feed sends.
    """
    raw = getattr(incident, "contained_pct", None) if incident is not None else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    if value > 1.0:
        value = value / 100.0
    return min(1.0, value)


def containment_label(fraction: float | None) -> str:
    if fraction is None:
        return "containment unknown"
    return f"{fraction * 100:.0f}% contained"
