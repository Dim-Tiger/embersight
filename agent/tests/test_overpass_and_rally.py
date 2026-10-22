"""Overpass policy (serial, retry/backoff, mirrors, cache) and rally ranking."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from embersight_agent.tools import overpass, rally_points

BBOX = (34.40, -119.80, 34.50, -119.60)
QUERY = overpass._build_query(BBOX, {"amenity": ["fire_station"]})
OK_BODY = {"elements": [{"type": "node", "id": 1, "lat": 34.45, "lon": -119.7, "tags": {}}]}


class FakeOverpass:
    """Scripted responses for httpx.AsyncClient.send; records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.urls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def send(self, client, request, *args, **kwargs):  # noqa: ANN001
        self.urls.append(str(request.url))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        status, body, headers = self.script.pop(0) if self.script else (200, OK_BODY, {})
        if status == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(status, json=body, headers=headers, request=request)


@pytest.fixture
def fake(monkeypatch):
    def install(script):
        f = FakeOverpass(script)

        async def send(self, request, *a, **k):  # noqa: ANN001
            return await f.send(self, request, *a, **k)

        monkeypatch.setattr(httpx.AsyncClient, "send", send)
        return f

    return install


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []

    async def no_wait(seconds):  # noqa: ANN001
        waited.append(seconds)

    monkeypatch.setattr(overpass, "_sleep", no_wait)
    return waited


def test_429_backs_off_then_rotates_to_a_mirror(fake, sleeps):
    f = fake([(429, {}, {"Retry-After": "3"}), (200, OK_BODY, {})])
    data = asyncio.run(overpass._post(QUERY))
    assert data == OK_BODY
    assert sleeps == [3.0], "must honour Retry-After"
    assert f.urls[0] != f.urls[1], "second attempt goes to the next mirror"
    assert all("User-Agent" for _ in f.urls)


def test_attempts_are_bounded(fake, sleeps):
    f = fake([(503, {}, {})] * 10)
    with pytest.raises(overpass.OverpassUnavailable):
        asyncio.run(overpass._post(QUERY))
    assert len(f.urls) == overpass.MAX_ATTEMPTS
    assert all(s <= overpass.MAX_BACKOFF_S for s in sleeps)


def test_bad_request_is_not_retried(fake, sleeps):
    f = fake([(400, {"remark": "syntax"}, {})])
    with pytest.raises(overpass.OverpassUnavailable):
        asyncio.run(overpass._post(QUERY))
    assert len(f.urls) == 1 and sleeps == []


def test_requests_never_run_in_parallel(fake, sleeps):
    f = fake([])

    async def many():
        queries = [overpass._build_query(BBOX, {"amenity": [f"x{i}"]}) for i in range(4)]
        await asyncio.gather(*(overpass._post(q) for q in queries))

    asyncio.run(many())
    assert len(f.urls) == 4
    assert f.max_in_flight == 1


def test_repeat_query_is_served_from_disk_cache(fake, sleeps):
    f = fake([(200, OK_BODY, {})])
    asyncio.run(overpass._post(QUERY))
    asyncio.run(overpass._post(QUERY))
    assert len(f.urls) == 1


def test_stale_cache_is_used_when_every_mirror_is_down(fake, sleeps, monkeypatch):
    fake([(200, OK_BODY, {})])
    asyncio.run(overpass._post(QUERY))
    monkeypatch.setattr(overpass, "CACHE_TTL_S", -1.0)  # everything is now stale
    fake([("timeout", None, {})] * 10)
    assert asyncio.run(overpass._post(QUERY)) == OK_BODY


def test_union_query_carries_every_clause():
    q = overpass._build_union_query(
        BBOX, [("natural", ["water"], ("node",)), ("waterway", ["river", "stream"], ("way",))]
    )
    assert 'node["natural"="water"]' in q
    assert 'way["waterway"~"^(river|stream)$"]' in q
    assert q.count("out center tags") == 1


# ---- rally points --------------------------------------------------------------

INCIDENT = (34.45, -119.70)


def _pt(name, rtype, lat, lon, source="osm", capacity=None):
    return {"name": name, "rally_type": rtype, "loc": (lat, lon), "source": source,
            "capacity": capacity}


def test_rally_failures_are_reported_not_hidden(sleeps):
    block = asyncio.run(
        rally_points.discover_rally_points(BBOX, INCIDENT, "Test Fire", wind_from_deg=270.0)
    )
    assert block["points"] == []
    assert {"osm", "hifld"} <= set(block["source_failures"])


def test_hospitals_are_never_rally_points():
    ranked = rally_points.rank_rally_points(
        [_pt("Mercy", "hospital", 34.45, -119.80), _pt("High School", "school", 34.45, -119.81)],
        INCIDENT, None,
    )
    assert [p["name"] for p in ranked] == ["High School"]


def test_upwind_point_outranks_identical_downwind_point():
    # Wind FROM the west: west of the fire is upwind (safe), east is downwind.
    west = _pt("West Gym", "school", 34.45, -119.81)
    east = _pt("East Gym", "school", 34.45, -119.59)
    ranked = rally_points.rank_rally_points([east, west], INCIDENT, wind_from_deg=270.0)
    assert ranked[0]["name"] == "West Gym"
    assert ranked[0]["wind_relation"] == "upwind"
    assert ranked[1]["wind_relation"] == "downwind"


def test_duplicate_points_keep_the_higher_priority_type():
    a = _pt("Community Ctr", "community_ctr", 34.45, -119.81)
    b = _pt("Shelter", "shelter", 34.4505, -119.8102)  # ~60 m away
    ranked = rally_points.rank_rally_points([a, b], INCIDENT, None)
    assert [p["name"] for p in ranked] == ["Shelter"]


def test_distance_band_excludes_points_too_close_to_the_fire():
    assert rally_points._distance_score(2.0) == 0.0
    assert rally_points._distance_score(12.0) == 1.0
    assert rally_points._distance_score(31.0) == 0.0
