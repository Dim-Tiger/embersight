"""Values-at-Risk rollups: unknown is not zero, and people match residences."""

from __future__ import annotations

import asyncio
import json

import pytest

from embersight_agent.agents import values_at_risk as var
from embersight_agent.state import AgentState, Incident
from embersight_agent.tools import arcgis, buildings, infra

SQUARE = "POLYGON ((-119.72 34.42, -119.68 34.42, -119.68 34.46, -119.72 34.46, -119.72 34.42))"


def _ok_tallies(**overrides):
    tallies = {
        "ms_buildings": {"count": 410, "total_footprint_sqm": 81000.0},
        "usa_structures": {
            "total": 400,
            "by_occupancy": {"Residential": 300, "Commercial": 60, "Public": 40},
        },
        "hospitals": [{"name": "Valley Med", "beds": 120, "lat": 34.43, "lon": -119.7}],
        "schools": [{"name": "Ridge Elementary", "enrollment": 450, "lat": 34.44, "lon": -119.69}],
        "transmission": [{"voltage_kv": 230.0}, {"voltage_kv": 69.0}],
        "critical_facilities": {"fire_stations": [{}], "eocs": [], "comm_towers": [{}, {}], "total": 3},
    }
    tallies.update(overrides)
    return tallies


def test_population_comes_from_counted_residences():
    roll = var._rollup(_ok_tallies(), SQUARE)
    assert roll["residential_count"] == 300
    assert roll["population_estimate"] == 750  # 300 x 2.5
    assert roll["population_method"] == "residential_structures"
    assert roll["structures_total"] == 410
    assert roll["transmission_max_kv"] == 230.0
    assert roll["unavailable"] == []


def test_failed_structure_layer_is_unknown_and_population_is_labelled_estimate():
    """The DOME run printed '~35,895 people, 0 residences': the structure
    layer had failed, so residences were unknown and the population was a
    flat area-density guess presented as fact."""
    roll = var._rollup(
        _ok_tallies(
            usa_structures={"total": 0, "by_occupancy": {}, "error": "http:502"},
            ms_buildings={"count": 0, "total_footprint_sqm": 0.0, "error": "http:502"},
        ),
        SQUARE,
    )
    assert roll["residential_count"] is None
    assert roll["structures_total"] is None
    assert roll["population_method"] == "area_density_estimate"
    assert roll["population_estimate"] > 0
    text = " ".join(var._key_findings(roll))
    assert "rough area-density estimate" in text
    assert "residential structures unavailable" in text


def test_critical_facility_errors_are_not_counted_as_facilities():
    # Every sub-layer fails (network is blocked in tests). The old code put an
    # {"error": ...} row in each list and counted it, reporting 3 facilities.
    result = infra.query_critical_facilities(SQUARE)
    assert result["total"] == 0
    assert set(result["errors"]) == {"fire_stations", "eocs", "comm_towers"}
    assert var._has_error(result)
    roll = var._rollup(_ok_tallies(critical_facilities=result), SQUARE)
    assert roll["critical_facilities_total"] is None


def test_confidence_all_sources_ok():
    tallies = _ok_tallies()
    conf, driver = var._confidence(tallies, var._rollup(tallies, SQUARE))
    assert conf == pytest.approx(0.9)
    assert "all 6" in driver


def test_zero_counts_with_a_failed_source_are_not_a_confident_all_clear():
    """'80% confidence, 1/6 sources errored, all zeros' must not happen."""
    tallies = _ok_tallies(
        ms_buildings={"count": 0, "total_footprint_sqm": 0.0},
        usa_structures={"total": 0, "by_occupancy": {}},
        hospitals=[{"error": "http:timeout"}],
        schools=[],
        transmission=[],
        critical_facilities={"fire_stations": [], "eocs": [], "comm_towers": [], "total": 0},
    )
    conf, driver = var._confidence(tallies, var._rollup(tallies, SQUARE))
    assert conf <= 0.4
    assert "not an all-clear" in driver


def test_core_layer_failure_caps_confidence():
    tallies = _ok_tallies(usa_structures={"total": 0, "by_occupancy": {}, "error": "x"})
    conf, driver = var._confidence(tallies, var._rollup(tallies, SQUARE))
    assert conf <= 0.5
    assert "structure counts unknown" in driver


def test_run_with_every_feed_down_reports_partial_not_zero():
    state = AgentState(incident=Incident(id="V", name="Offline Fire", lat=34.44, lon=-119.7))
    out = asyncio.run(var.run(state))["outputs"]["values_at_risk"]
    roll = out.payload["rollup"]
    assert len(roll["unavailable"]) == 6
    assert roll["residential_count"] is None and roll["hospitals_count"] is None
    assert out.confidence <= 0.4
    assert out.narrative.startswith("PARTIAL inventory")


# ---- ArcGIS spatial filter ---------------------------------------------------


def test_polygon_filter_is_sent_as_polygon_not_bbox():
    params = arcgis.geometry_params(SQUARE)
    assert params["geometryType"] == "esriGeometryPolygon"
    rings = json.loads(params["geometry"])["rings"]
    assert rings[0][0] == [-119.72, 34.42]


def test_multipolygon_filter_keeps_every_part():
    multi = (
        "MULTIPOLYGON (((0 0, 1 0, 1 1, 0 0)), ((5 5, 6 5, 6 6, 5 5)))"
    )
    rings = json.loads(arcgis.geometry_params(multi)["geometry"])["rings"]
    assert len(rings) == 2


def test_bbox_filter_is_an_envelope():
    params = arcgis.geometry_params((-1.0, -2.0, 3.0, 4.0))
    assert params["geometryType"] == "esriGeometryEnvelope"
    assert json.loads(params["geometry"])["xmax"] == 3.0


def test_building_count_is_not_capped_at_one_page(monkeypatch):
    """The old query downloaded features (max 2,000) and counted them."""
    def fake_query(url, params, **kw):  # noqa: ANN001
        assert "outStatistics" in params
        return {"features": [{"attributes": {"n": 5598, "sqm": 1234567.0}}]}

    monkeypatch.setattr(buildings, "query", fake_query)
    result = buildings.query_ms_buildings(SQUARE)
    assert result == {"count": 5598, "total_footprint_sqm": 1234567.0}


def test_arcgis_error_body_is_a_failure_not_zero(monkeypatch):
    def fake_query(url, params, **kw):  # noqa: ANN001
        raise RuntimeError("arcgis:Invalid URL")

    monkeypatch.setattr(buildings, "query", fake_query)
    assert "error" in buildings.query_usa_structures(SQUARE)
