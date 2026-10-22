"""Evacuation Intelligence proposals and zone-catalog parsing."""

from __future__ import annotations

import asyncio
import math

import pytest
from shapely import wkt as shapely_wkt

from embersight_agent.agents import evacuation_intelligence as ei
from embersight_agent.state import AgentOutput, AgentState, Incident
from embersight_agent.tools import evac as evac_tools
from embersight_agent.tools import zone_catalog as zc

LAT, LON = 38.5, -121.5


def _square(cx: float, cy: float, half: float) -> str:
    return (
        f"POLYGON (({cx - half} {cy - half}, {cx + half} {cy - half}, "
        f"{cx + half} {cy + half}, {cx - half} {cy + half}, {cx - half} {cy - half}))"
    )


def _disk(r: float) -> str:
    pts = [(LON + r * math.cos(2 * math.pi * i / 24), LAT + r * math.sin(2 * math.pi * i / 24))
           for i in range(24)]
    pts.append(pts[0])
    return "POLYGON ((" + ", ".join(f"{x} {y}" for x, y in pts) + "))"


def _zone(zid: str, wkt: str, **extra) -> dict:
    return {"zone_id": zid, "name": f"Zone {zid}", "current_status": "NORMAL",
            "polygon_wkt": wkt, "jurisdiction": "Test County", **extra}


def _run_ei(monkeypatch, zones: list[dict]) -> tuple[dict, list[dict]]:
    async def fake_zones(bbox):  # noqa: ANN001
        return zones

    async def no_overlay(bbox):  # noqa: ANN001
        return {}

    envelopes: list[dict] = []

    def approve(kind, payload):  # noqa: ANN001
        envelopes.append(payload)
        return {"decision": "approved", "actor": "test"}

    monkeypatch.setattr(zc, "fetch_static_zones_in_bbox", fake_zones)
    monkeypatch.setattr(evac_tools, "get_active_status_overlay", no_overlay)
    monkeypatch.setattr(ei, "request_human_decision", approve)

    spread = AgentOutput(agent="spread_simulation", narrative="",
                         payload={"cones": {"6h": _disk(0.01), "12h": _disk(0.05), "24h": _disk(0.1)}})
    state = AgentState(
        incident=Incident(id="E", name="Evac Fire", lat=LAT, lon=LON),
        outputs={"spread_simulation": spread},
    )
    return asyncio.run(ei.run(state)), envelopes


def test_proposal_envelope_has_residence_estimate_and_population_source(monkeypatch):
    """residential_structures_estimate was always 0: the code divided by an
    undefined DEFAULT_HOUSEHOLD_SIZE and a bare except swallowed the NameError."""
    patch, envelopes = _run_ei(monkeypatch, [_zone("Z1", _square(LON, LAT, 0.005))])
    assert len(envelopes) == 1
    impact = envelopes[0]["impact"]
    assert impact["human_displacement_estimate"] > 0
    assert impact["residential_structures_estimate"] == pytest.approx(
        impact["human_displacement_estimate"] / evac_tools.DEFAULT_HOUSEHOLD_SIZE, abs=1
    )
    assert envelopes[0]["population_method"] == "area_density_estimate"
    assert len(patch["audit_log"]) == 1


def test_published_zone_population_beats_the_density_guess(monkeypatch):
    zone = _zone("Z1", _square(LON, LAT, 0.005), population_static=1234, structures_static=480)
    _, envelopes = _run_ei(monkeypatch, [zone])
    assert envelopes[0]["population_estimate"] == 1234
    assert envelopes[0]["impact"]["residential_structures_estimate"] == 480
    assert envelopes[0]["population_method"] == "zone_catalog"


def test_untouched_zone_gets_no_proposal(monkeypatch):
    far = _zone("FAR", _square(LON + 0.4, LAT + 0.4, 0.01))
    patch, envelopes = _run_ei(monkeypatch, [far])
    assert envelopes == []
    assert patch["outputs"]["evacuation_intelligence"].payload["counts"]["proposed"] == 0


@pytest.mark.parametrize(
    "current,overlaps,egress,expected",
    [
        ("NORMAL", {"6h": 0.6, "12h": 0.9, "24h": 1.0}, {}, "ORDER"),
        ("NORMAL", {"6h": 0.0, "12h": 0.15, "24h": 0.2}, {}, "WARNING"),
        ("NORMAL", {"6h": 0.0, "12h": 0.0, "24h": 0.35}, {}, "WARNING"),
        ("NORMAL", {"6h": 0.0, "12h": 0.05, "24h": 0.1}, {}, "NORMAL"),
        ("NORMAL", {"6h": 0.0, "12h": 0.0, "24h": 0.0}, {"clear": False}, "ORDER"),
        ("WARNING", {"6h": 0.1, "12h": 0.5, "24h": 0.9}, {}, "WARNING"),
        ("ORDER", {"6h": 0.0, "12h": 0.0, "24h": 0.0}, {}, "ORDER"),
    ],
)
def test_deterministic_proposal_thresholds(current, overlaps, egress, expected):
    status, _ = ei._deterministic_proposal({"current_status": current}, overlaps, egress)
    assert status == expected


# ---- zone catalog ---------------------------------------------------------------


def test_multipolygon_zone_keeps_every_part():
    geom = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[5, 5], [5.5, 5], [5.5, 5.5], [5, 5.5], [5, 5]]],
        ],
    }
    wkt = zc._geojson_geom_to_wkt(geom)
    assert shapely_wkt.loads(wkt).area == pytest.approx(1.25)
    assert zc._wkt_bbox(wkt) == (0.0, 0.0, 5.5, 5.5)


def test_objectid_fallback_ids_do_not_collide_across_sources():
    feat = {"properties": {"OBJECTID": 1, "NAME": "A"},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
    a = zc._parse_feature_esri_geojson(feat, "county_a", "A County")
    b = zc._parse_feature_esri_geojson(feat, "county_b", "B County")
    assert a["zone_id"] != b["zone_id"]
    real = {"properties": {"ZONE_ID": "VEN-123", "OBJECTID": 9}, "geometry": feat["geometry"]}
    assert zc._parse_feature_esri_geojson(real, "county_a", "A")["zone_id"] == "VEN-123"


def test_genasys_name_falls_back_to_cross_streets():
    feat = {
        "properties": {"zone_id": "LOS-1", "north_of": "Kanan Rd, Mulholland",
                       "east_of": "Lobo Canyon Rd", "county_abbr": "XLA", "night_population": 812},
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    }
    z = zc._parse_feature_genasys(feat, "genasys", "unknown")
    assert z["name"] == "north of Kanan Rd, east of Lobo Canyon Rd"
    assert z["jurisdiction"] == "Los Angeles County"
    assert z["population_static"] == 812


def test_zone_fetch_offline_returns_empty_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(zc, "CACHE_DIR", tmp_path)
    zones = asyncio.run(zc.fetch_static_zones_in_bbox((-121.6, 38.4, -121.4, 38.6)))
    assert zones == []
