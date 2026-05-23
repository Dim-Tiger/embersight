"""OSMnx + networkx ingress/egress routing and staging-area scoring."""

from __future__ import annotations

from typing import Any


async def compute_ingress_egress(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("pass-2: implement with osmnx + networkx")


async def score_staging_areas(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("pass-2: paved surface + water proximity + comms LOS")
