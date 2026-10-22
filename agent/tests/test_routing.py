"""Routing & staging: standoff, scoring, wind-aware egress ordering."""

from __future__ import annotations

import asyncio
import math

import pytest

from embersight_agent.agents import routing_staging as rs
from embersight_agent.state import AgentOutput, AgentState, Incident
from embersight_agent.tools import overpass, routing

INCIDENT = (34.45, -119.70)
KM_PER_DEG_LON = 111.32 * math.cos(math.radians(34.45))


def _east_of_incident(km: float) -> tuple[float, float]:
    return INCIDENT[0], INCIDENT[1] + km / KM_PER_DEG_LON


def test_standoff_grows_with_fire_size():
    assert routing.standoff_km(None) == pytest.approx(2.0)
    assert routing.standoff_km(0) == pytest.approx(2.0)
    # 6,788 ac = 27.5 km², equivalent radius 2.96 km
    assert routing.standoff_km(6788) == pytest.approx(4.96, abs=0.02)


def test_large_fire_rejects_staging_that_a_small_fire_allows():
    lat, lon = _east_of_incident(3.0)
    paved = [{"lat": lat, "lon": lon, "tags": {"name": "Lot 3km"}}]
    small = rs._pick_candidate_locations(paved, [], INCIDENT, standoff=routing.standoff_km(50))
    large = rs._pick_candidate_locations(paved, [], INCIDENT, standoff=routing.standoff_km(6788))
    assert [c["name"] for c in small] == ["Lot 3km"]
    assert large == [], "3 km from the ignition is inside a 6,788 ac fire"


def test_incident_distance_tent_shifts_with_standoff():
    assert routing._incident_distance_score(1.9) == 0.0
    assert routing._incident_distance_score(5.0) == pytest.approx(1.0)
    assert routing._incident_distance_score(15.0) == pytest.approx(0.0)
    assert routing._incident_distance_score(4.0, standoff=5.0) == 0.0
    assert routing._incident_distance_score(8.0, standoff=5.0) == pytest.approx(1.0)


def test_staging_score_prefers_upwind_and_stays_in_unit_interval():
    west = (INCIDENT[0], INCIDENT[1] - 6 / KM_PER_DEG_LON)
    east = _east_of_incident(6)
    kw = dict(paved_areas=[], water=[], fire_stations=[], dem_elevation=300.0,
              incident_latlon=INCIDENT, wind_from_deg=270.0)
    up = routing.score_staging_candidate_detailed(loc_latlon=west, **kw)
    down = routing.score_staging_candidate_detailed(loc_latlon=east, **kw)
    assert up["components"]["wind"] == pytest.approx(1.0)
    assert down["components"]["wind"] == pytest.approx(0.0)
    assert 0.0 <= down["total"] < up["total"] <= 1.0
    assert sum(up["weights"].values()) == pytest.approx(1.0)


def test_egress_bearings_start_upwind():
    ranked = rs._rank_bearings_by_wind(rs._EGRESS_BEARINGS_DEG, wind_from_deg=270.0)
    assert ranked[0][0] == "W" and ranked[-1][0] == "E"
    assert rs._wind_relation_tag(270.0, 270.0) == "upwind"
    assert rs._wind_relation_tag(0.0, 270.0) == "crosswind"
    assert rs._wind_relation_tag(90.0, 270.0) == "downwind"


@pytest.mark.parametrize(
    "tag,kph",
    [("35 mph", 56.33), ("80", 80.0), ("50 km/h", 50.0), (["25 mph", "45 mph"], 72.42),
     ("signals", None), (None, None)],
)
def test_maxspeed_parsing(tag, kph):
    got = routing._parse_maxspeed(tag)
    assert (got is None) if kph is None else got == pytest.approx(kph, abs=0.01)


def test_overpass_down_degrades_to_labelled_fallback(monkeypatch):
    async def no_wait(_s):  # noqa: ANN001
        return None

    monkeypatch.setattr(overpass, "_sleep", no_wait)
    state = AgentState(
        incident=Incident(id="r", name="Route Fire", lat=INCIDENT[0], lon=INCIDENT[1], acres=50),
        outputs={"weather_wind": AgentOutput(agent="weather_wind", narrative="",
                                             payload={"wind_dir_deg": 270.0, "wind_speed_mph": 10})},
    )
    out = asyncio.run(rs.run(state))["outputs"]["routing_staging"]
    assert out.confidence <= 0.1
    assert out.payload["candidates"] == []
    assert "SUGGEST manual selection" in out.narrative
