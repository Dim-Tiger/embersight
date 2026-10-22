"""Spread Simulation: fallback ROS math, cone geometry, and IC wording."""

from __future__ import annotations

import asyncio
import math

import pytest

from embersight_agent.agents import spread_simulation
from embersight_agent.state import AgentOutput, AgentState, Incident
from embersight_agent.tools import pyretechnics_spread as ps

FM = {"1h": 6.0, "10h": 8.0, "100h": 10.0}


def _ros(fuel: str, wind_mph: float = 10.0, slope_pct: float = 0.0) -> float:
    return ps.calc_surface_ros(fuel, slope_pct, 0.0, wind_mph, 90.0, FM)["ros_max_fpm"]


# ---- fuel model parsing ---------------------------------------------------


def test_landfire_label_resolves_to_its_own_fuel_model():
    """terrain_fuel publishes '102 GR2 low load, dry climate grass'. That used
    to miss the lookup table and silently run as GS2."""
    assert ps.fuel_code("102 GR2 low load, dry climate grass") == "GR2"
    assert _ros("102 GR2 low load, dry climate grass") == pytest.approx(_ros("GR2"))
    assert _ros("GR2") != pytest.approx(_ros("GS2"))


@pytest.mark.parametrize("label", ["98 NB8 open water", "91 NB1 urban/developed", "NB9"])
def test_non_burnable_fuel_does_not_spread(label):
    assert _ros(label, wind_mph=30.0) == 0.0


def test_unknown_fuel_model_is_reported_as_substituted():
    preset = ps.fuel_preset("165 TU5 very high load, dry timber-shrub")
    assert preset.code == "TU5" and not preset.substituted
    preset = ps.fuel_preset("162 TU2 moderate load, humid timber-shrub")
    assert preset.substituted and preset.code == "GS2"


# ---- ROS / ellipse physics sanity ------------------------------------------


def test_ros_increases_with_wind_and_slope():
    assert _ros("SH5", wind_mph=20) > _ros("SH5", wind_mph=5) > _ros("SH5", wind_mph=0)
    assert _ros("SH5", slope_pct=40) > _ros("SH5", slope_pct=0)


def test_fuel_at_moisture_of_extinction_does_not_spread():
    wet = ps.calc_surface_ros("GR2", 0, 0, 20, 90, {"1h": 16.0})
    assert wet["ros_max_fpm"] == 0.0


def test_anderson_lwr_is_one_in_calm_and_capped_at_eight():
    assert ps.anderson_lwr_from_wind(0) == pytest.approx(1.0, abs=0.01)
    assert ps.anderson_lwr_from_wind(60) == 8.0


def test_ellipse_head_distance_matches_ros_times_time():
    # 10 ft/min for 2 h = 1200 ft = 365.76 m from the ignition to the head.
    ell = ps.anderson_ellipse(10.0, 2.0, 2.0)
    minx, _, maxx, _ = ell.bounds
    assert minx == pytest.approx(0.0, abs=1.0)
    assert maxx == pytest.approx(365.76, rel=0.01)


def test_cone_points_downwind():
    """Wind FROM the west (270°) must push the head EAST (+x)."""
    mc = ps.monte_carlo_cone(
        {"fuel_model": "GR2", "slope_pct": 0, "aspect_deg": 0, "wind_speed_mph": 15,
         "wind_dir_deg": 90.0, "fuel_moisture": FM},
        n=20, hours=(6,),
    )
    centroid = mc[6]["bands"][1].centroid
    assert centroid.x > 0 and abs(centroid.y) < centroid.x


# ---- the agent -------------------------------------------------------------


def _state(contained=None, weather_payload=None) -> AgentState:
    return AgentState(
        incident=Incident(id="S-1", name="Spread Fire", lat=34.5, lon=-119.7,
                          acres=500, contained_pct=contained),
        outputs={
            "weather_wind": AgentOutput(
                agent="weather_wind", narrative="",
                payload=weather_payload
                if weather_payload is not None
                else {"wind_speed_mph": 15.0, "wind_dir_deg": 270.0, "fuel_moisture": {}},
            ),
            "terrain_fuel": AgentOutput(
                agent="terrain_fuel", narrative="",
                payload={"fuel_model": "102 GR2 low load, dry climate grass",
                         "slope_pct": 10.0, "aspect_deg": 90.0},
            ),
        },
    )


def test_fully_contained_fire_gets_no_free_spread_projection():
    patch = asyncio.run(spread_simulation.run(_state(contained=1.0)))
    out = patch["outputs"]["spread_simulation"]
    assert out.payload["containment_gated"] is True
    assert out.payload["cones"]["24h"] is None
    assert "100% contained" in out.narrative
    assert out.confidence <= 0.2


def test_partially_contained_fire_is_qualified():
    patch = asyncio.run(spread_simulation.run(_state(contained=0.6)))
    out = patch["outputs"]["spread_simulation"]
    assert out.payload["cones"]["24h"] is not None
    assert "60% contained" in out.narrative
    assert "does not model containment lines" in out.narrative


def test_narrative_states_wind_as_mph_from_cardinal(monkeypatch):
    seen: dict = {}

    async def capture(context):  # noqa: ANN001
        seen["context"] = context
        return None

    monkeypatch.setattr(spread_simulation, "_llm_narrate", capture)
    out = asyncio.run(spread_simulation.run(_state()))["outputs"]["spread_simulation"]
    # Wind FROM 270 (west). The LLM context used to say "from 90°", the
    # fire-head TO direction, which reverses the picture for the model.
    assert "15 mph from W (270°)" in seen["context"]
    assert "15 mph from W (270°)" in out.narrative
    assert out.payload["fuel_model"] == "GR2"


def test_missing_weather_is_labelled_as_assumed():
    out = asyncio.run(spread_simulation.run(_state(weather_payload={})))
    out = out["outputs"]["spread_simulation"]
    assert set(out.payload["assumed_inputs"]) >= {"wind_speed_mph", "wind_dir_deg"}
    assert "ASSUMED" in out.narrative


def test_cone_impact_labels_density_population_and_unknown_counts():
    roll = {
        "population_estimate": 35895,
        "population_method": "area_density_estimate",
        "residential_count": None,
        "structures_total": None,
        "hospitals_count": 0,
        "schools_count": 0,
        "critical_facilities_total": 0,
    }
    text = spread_simulation._impact_sentence(roll)
    assert "rough area-density estimate" in text
    assert "residences unavailable" in text
    assert "0 residences" not in text
