"""Shared pytest setup: keep every test hermetic.

The agents call a dozen public services (ArcGIS, NWS, Overpass, LANDFIRE,
Anthropic). Unit tests must not depend on any of them being up, so this
autouse fixture makes every outbound HTTP request fail fast with a
connection error. Each tool then takes its own degraded path, which is
exactly the behaviour the IC sees when a feed is down.
"""

from __future__ import annotations

import httpx
import pytest


class NetworkBlocked(httpx.ConnectError):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def _blocked_sync(self, request, *args, **kwargs):  # noqa: ANN001
        raise NetworkBlocked(f"network disabled in tests: {request.url}", request=request)

    async def _blocked_async(self, request, *args, **kwargs):  # noqa: ANN001
        raise NetworkBlocked(f"network disabled in tests: {request.url}", request=request)

    monkeypatch.setattr(httpx.Client, "send", _blocked_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_async)

    try:
        import requests

        def _blocked_requests(*args, **kwargs):  # noqa: ANN001
            raise requests.ConnectionError("network disabled in tests")

        monkeypatch.setattr(requests.Session, "request", _blocked_requests)
    except ImportError:  # pragma: no cover
        pass

    # No LLM calls either, and no reads from a developer's on-disk caches.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("EMBERSIGHT_OVERPASS_CACHE", str(tmp_path / "overpass"))
    monkeypatch.setenv("EMBERSIGHT_ZONES_CACHE", str(tmp_path / "zones"))

    # Reset the Overpass circuit breaker so one test's outage can't leak
    # into the next.
    from embersight_agent.tools import overpass

    monkeypatch.setattr(overpass, "_down_until", 0.0)
    yield
