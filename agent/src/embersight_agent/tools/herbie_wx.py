"""HRRR / RTMA gridded weather via Herbie.

Pass-2 will pull 10-m UGRD/VGRD, 2-m TMP/RH, and interpolate to the
incident AOI. Stubbed here to keep import-time light during the hackathon.
"""

from __future__ import annotations

from typing import Any


async def fetch_hrrr_forecast(lat: float, lon: float, fxx: int = 6) -> dict[str, Any]:
    raise NotImplementedError("pass-2: install `science` extra and implement Herbie pull")
