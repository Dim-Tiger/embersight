"""Critical-infrastructure replacements for the decommissioned HIFLD portal
(see README §HIFLD CORRECTION).

  - Hospitals + nursing homes: CMS Provider of Services public files
  - Schools: NCES Common Core of Data
  - Electric transmission: HIFLD archive on DataLumos (DOI 10.3886/E241367V1)
    + EIA Form 860
  - Cell towers: FCC Antenna Structure Registration (ASR)
"""

from __future__ import annotations

from typing import Any


async def fetch_cms_hospitals(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    raise NotImplementedError("pass-2: download CMS POS file and filter")


async def fetch_nces_schools(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    raise NotImplementedError("pass-2: download NCES CCD and filter")


async def fetch_transmission_lines(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    raise NotImplementedError("pass-2: DataLumos HIFLD archive + EIA Form 860")
