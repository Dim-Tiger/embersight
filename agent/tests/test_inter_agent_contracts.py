"""Inter-agent payload contract tests.

These tests pin the SHAPES of payloads flowing between subagents so that
silent contract drift (the kind that lets each subagent's own smoke test
pass while the integrated graph degrades to defaults) trips CI instead of
shipping a green build.

Run with: `uv run pytest tests/`
"""

from __future__ import annotations

import asyncio

import pytest

from embersight_agent.agents import spread_simulation
from embersight_agent.agents.evacuation_intelligence import _coerce_geom
from embersight_agent.agents.terrain_fuel import (
    _dominant_aspect_deg,
    _dominant_fuel_code,
    _slope_deg_to_pct,
)
from embersight_agent.state import (
    AgentOutput,
    AgentState,
    CitationBundle,
    Incident,
)


def _output(agent: str, payload: dict, confidence: float = 0.7) -> AgentOutput:
    return AgentOutput(
        agent=agent,
        narrative=f"[mock] {agent}",
        payload=payload,
        confidence=confidence,
        citation_bundle=CitationBundle(),
    )


def _incident() -> Incident:
    return Incident(
        id="TEST-INC", name="Contract Test Fire",
        lat=34.5, lon=-119.7, acres=200.0, source="synthetic",
    )


def test_weather_wind_flat_keys_flow_to_spread():
    """spread_simulation must read top-level wind keys, not nested ones."""
    weather = _output(
        "weather_wind",
        {
            "wind_speed_mph": 22.5,
            "wind_dir_deg": 315.0,
            "temp_f": 92.0,
            "rh_pct": 12.0,
            "fuel_moisture": {"1h": 4.5, "10h": 7.0},
        },
    )
    terrain = _output(
        "terrain_fuel",
        {"fuel_model": "SH5", "slope_pct": 28.0, "aspect_deg": 180.0},
    )
    state = AgentState(
        incident=_incident(), operational_period=1,
        outputs={"weather_wind": weather, "terrain_fuel": terrain},
    )
    patch = asyncio.run(spread_simulation.run(state))
    out = patch["outputs"]["spread_simulation"]

    assert out.payload["wind_speed_mph"] == 22.5, (
        "spread_simulation must consume top-level wind_speed_mph from weather_wind"
    )
    assert out.payload["wind_dir_deg"] == 315.0
    assert out.payload["fuel_model"] == "SH5", (
        "spread_simulation must consume top-level fuel_model from terrain_fuel"
    )


def test_spread_cone_keys_are_strings_with_h_suffix():
    """Downstream consumers index cones with 'Nh' strings, not bare ints."""
    weather = _output(
        "weather_wind",
        {"wind_speed_mph": 15.0, "wind_dir_deg": 270.0, "fuel_moisture": {}},
    )
    terrain = _output(
        "terrain_fuel",
        {"fuel_model": "GS2", "slope_pct": 15.0, "aspect_deg": 225.0},
    )
    state = AgentState(
        incident=_incident(), operational_period=1,
        outputs={"weather_wind": weather, "terrain_fuel": terrain},
    )
    patch = asyncio.run(spread_simulation.run(state))
    cones = patch["outputs"]["spread_simulation"].payload["cones"]

    assert set(cones.keys()) == {"1h", "6h", "12h", "24h"}, (
        f"cones must be keyed by 'Nh' strings; got {sorted(cones.keys())!r}"
    )
    for key in ("1h", "6h", "12h", "24h"):
        assert isinstance(cones[key], dict) or cones[key] is None, (
            f"cones[{key!r}] must be a single GeoJSON dict (or None), "
            f"not {type(cones[key]).__name__}"
        )


def test_spread_cone_bands_expose_four_quantiles():
    """cone_bands must keep the p25/p50/p75/p95 quantile detail for the UI."""
    weather = _output(
        "weather_wind",
        {"wind_speed_mph": 15.0, "wind_dir_deg": 270.0, "fuel_moisture": {}},
    )
    terrain = _output(
        "terrain_fuel",
        {"fuel_model": "GS2", "slope_pct": 15.0, "aspect_deg": 225.0},
    )
    state = AgentState(
        incident=_incident(), operational_period=1,
        outputs={"weather_wind": weather, "terrain_fuel": terrain},
    )
    patch = asyncio.run(spread_simulation.run(state))
    bands = patch["outputs"]["spread_simulation"].payload["cone_bands"]

    assert set(bands.keys()) == {"1h", "6h", "12h", "24h"}
    for key in ("1h", "6h", "12h", "24h"):
        assert set(bands[key].keys()) == {"p25", "p50", "p75", "p95"}, (
            f"cone_bands[{key!r}] must have all 4 quantile keys"
        )


def test_evacuation_intelligence_can_coerce_spread_cone():
    """The cone shape emitted by spread must coerce to a shapely geometry."""
    weather = _output(
        "weather_wind",
        {"wind_speed_mph": 20.0, "wind_dir_deg": 315.0, "fuel_moisture": {}},
    )
    terrain = _output(
        "terrain_fuel",
        {"fuel_model": "SH5", "slope_pct": 28.0, "aspect_deg": 180.0},
    )
    state = AgentState(
        incident=_incident(), operational_period=1,
        outputs={"weather_wind": weather, "terrain_fuel": terrain},
    )
    patch = asyncio.run(spread_simulation.run(state))
    cone_24h = patch["outputs"]["spread_simulation"].payload["cones"]["24h"]

    geom = _coerce_geom(cone_24h)
    assert geom is not None, "evacuation_intelligence must coerce spread.cones[24h]"
    assert geom.is_valid
    assert geom.area > 0, "non-degenerate cone area expected"


def test_terrain_fuel_flat_projections_have_safe_defaults():
    """Empty/errored LANDFIRE payload must still yield usable flat defaults."""
    empty_terrain = {
        "error": "no module named 'landfire'",
        "pixels": 0,
        "slope_deg": {},
        "aspect_distribution": {},
        "elevation_m": {},
    }
    empty_fuel = {
        "error": "no module named 'landfire'",
        "pixels": 0,
        "class_distribution": {},
        "dominant_classes": [],
        "purity": 0.0,
    }
    assert _dominant_fuel_code(empty_fuel) == "GS2"
    assert _slope_deg_to_pct(empty_terrain) == 15.0
    assert _dominant_aspect_deg(empty_terrain) == 225.0


def test_terrain_fuel_flat_projections_use_real_data_when_present():
    """Live LANDFIRE-shaped payload projects to flat keys correctly."""
    terrain = {
        "pixels": 1000,
        # slope_deg.mean = 18° → tan(18°)*100 ≈ 32.5%
        "slope_deg": {"mean": 18.0, "p90": 30.0},
        "aspect_distribution": {"NE": 0.1, "SW": 0.5, "S": 0.4},
        "elevation_m": {"mean": 400.0},
    }
    fuel = {
        "pixels": 1000,
        "class_distribution": {"SH5": 0.7, "GR1": 0.3},
        "dominant_classes": [{"code": "SH5", "fraction": 0.7}],
        "purity": 0.7,
    }
    assert _dominant_fuel_code(fuel) == "SH5"
    # tan(18°) * 100 = 32.49
    assert abs(_slope_deg_to_pct(terrain) - 32.5) < 0.1
    # SW dominant
    assert _dominant_aspect_deg(terrain) == 225.0
