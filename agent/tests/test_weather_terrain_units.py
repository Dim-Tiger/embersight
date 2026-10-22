"""Weather / terrain fallbacks and unit helpers."""

from __future__ import annotations

import asyncio
import math

import pytest

from embersight_agent.agents import terrain_fuel, weather_wind
from embersight_agent.state import AgentState, Incident
from embersight_agent.tools import herbie_wx, landfire
from embersight_agent.units import cardinal_16, containment_fraction, wind_phrase

NWS_FORECAST = {
    "forecast": {
        "properties": {
            "periods": [
                {
                    "windSpeed": "5 to 10 mph",
                    "windDirection": "WSW",
                    "temperature": 75,
                    "temperatureUnit": "F",
                    "relativeHumidity": {"value": 30},
                }
            ]
        }
    }
}


# ---- units -------------------------------------------------------------------


@pytest.mark.parametrize(
    "deg,label",
    [(0, "N"), (11.2, "N"), (11.3, "NNE"), (101, "E"), (115, "ESE"), (270, "W"), (359, "N"), (-90, "W")],
)
def test_cardinal_16(deg, label):
    assert cardinal_16(deg) == label


def test_wind_phrase_is_mph_from_cardinal():
    assert wind_phrase(14.8, 115) == "15 mph from ESE (115°)"
    assert wind_phrase(None, None) == "unknown speed from an unknown direction"


@pytest.mark.parametrize(
    "raw,expected",
    [(1, 1.0), (1.0, 1.0), (0.25, 0.25), (0, 0.0), (10.0, 0.10), (100, 1.0), (None, None), (-1, None)],
)
def test_containment_fraction_handles_both_scales(raw, expected):
    inc = Incident(id="c", name="c", lat=0, lon=0, contained_pct=raw)
    got = containment_fraction(inc)
    assert got == (pytest.approx(expected) if expected is not None else None)


# ---- weather -------------------------------------------------------------------


def test_no_sources_means_every_flat_key_is_labelled_assumed():
    flat = weather_wind._flat_wx_keys({}, [], None, {"stations": []}, None)
    assert flat["wind_speed_mph"] == 15.0 and flat["wind_dir_deg"] == 270.0
    assert set(flat["assumed_inputs"]) == {"wind_speed_mph", "wind_dir_deg", "temp_f", "rh_pct"}
    assert set(flat["sources"].values()) == {"planning_default"}


def test_nws_hourly_forecast_is_used_before_defaults():
    flat = weather_wind._flat_wx_keys({}, [], None, {"stations": []}, NWS_FORECAST)
    assert flat["wind_speed_mph"] == 10.0  # upper end of "5 to 10 mph"
    assert flat["wind_dir_deg"] == 247.5  # WSW
    assert flat["temp_f"] == 75.0 and flat["rh_pct"] == 30.0
    assert flat["assumed_inputs"] == []
    assert flat["sources"]["wind_dir_deg"] == "nws_hourly"


def test_rtma_beats_nws():
    rtma = {"wind_speed_mph": 22.0, "wind_direction_deg": 45.0, "temp_f": 88.0, "rh_pct": 9.0}
    flat = weather_wind._flat_wx_keys(rtma, [], None, {"stations": []}, NWS_FORECAST)
    assert flat["wind_speed_mph"] == 22.0 and flat["sources"]["wind_speed_mph"] == "rtma"


def test_offline_weather_run_says_wind_is_assumed():
    state = AgentState(incident=Incident(id="w", name="Wx Fire", lat=34.5, lon=-119.7))
    out = asyncio.run(weather_wind.run(state))["outputs"]["weather_wind"]
    assert "ASSUMED 15 mph from W (270°)" in out.narrative
    assert "wind_dir_deg" in out.payload["assumed_inputs"]


def test_synthetic_red_flag_alert_is_in_effect_when_shown():
    from datetime import datetime, timedelta

    from embersight_agent.state import TestOverrides

    inputs: dict = {"nws_alerts": []}
    weather_wind._apply_test_overrides(
        inputs, TestOverrides(enabled=True, alert_preset="red_flag_warning")
    )
    props = inputs["nws_alerts"][0]["properties"]
    effective = datetime.fromisoformat(props["effective"])
    expires = datetime.fromisoformat(props["expires"])
    assert expires - effective == timedelta(hours=weather_wind.SYNTHETIC_ALERT_HOURS)
    assert props["ends"] == props["expires"]


# ---- HRRR / RTMA grid-relative wind --------------------------------------------


HRRR = {"lov": 262.5, "latin1": 38.5, "latin2": 38.5}


def test_no_rotation_on_the_central_meridian():
    u, v = herbie_wx.earth_relative_uv(3.0, -4.0, lon=-97.5, **HRRR)
    assert (u, v) == pytest.approx((3.0, -4.0))


def test_grid_rotation_in_california_is_about_14_degrees():
    # Pure grid-north wind at the DOME fire longitude.
    u, v = herbie_wx.earth_relative_uv(0.0, 10.0, lon=-119.6, **HRRR)
    angle = math.degrees(math.atan2(u, v))  # compass bearing of the vector
    assert angle == pytest.approx(-(math.sin(math.radians(38.5)) * 22.1), abs=0.2)
    assert math.hypot(u, v) == pytest.approx(10.0)  # rotation keeps speed


def test_derive_applies_rotation_when_grib_says_grid_relative():
    grid = {"lon": -119.6, **HRRR}
    raw = {"wind_u_ms": 0.0, "wind_v_ms": -5.0, "_uv_grid": grid}  # grid-relative northerly
    rotated = herbie_wx._derive(raw)
    unrotated = herbie_wx._derive({"wind_u_ms": 0.0, "wind_v_ms": -5.0})
    assert unrotated["wind_direction_deg"] == pytest.approx(0.0)
    assert rotated["wind_direction_deg"] == pytest.approx(360 - 13.8, abs=0.3)


# ---- terrain -------------------------------------------------------------------


def test_terrain_offline_uses_labelled_defaults_and_no_stub_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(landfire, "CACHE_DIR", tmp_path / "landfire")
    state = AgentState(incident=Incident(id="t", name="Terrain Fire", lat=34.5, lon=-119.7))
    out = asyncio.run(terrain_fuel.run(state))["outputs"]["terrain_fuel"]
    assert "[stub]" not in out.narrative
    assert set(out.payload["assumed_inputs"]) == {"fuel_model", "slope_pct", "aspect_deg"}
    assert "ASSUMED defaults" in out.narrative
