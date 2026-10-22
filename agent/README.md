# embersight-agent

The FastAPI + LangGraph agent service behind the EmberSight dashboard. See the
repository root `README.md` for the full quickstart and architecture.

```bash
uv sync --python 3.12            # runtime deps + pytest (dev group)
uv run uvicorn embersight_agent.main:app --port 8000
uv run pytest -q                 # offline unit tests, no API key or network
```

Every agent has a deterministic fallback, so the service runs without an
`ANTHROPIC_API_KEY`; the LLM only rewrites the narrative text.

The optional `science` group (`uv sync --python 3.12 --group science`) adds
Herbie (HRRR/RTMA), SynopticPy (RAWS), OSMnx (road routing) and the LANDFIRE
client. It needs Python 3.11 or 3.12 because `pyretechnics` pins
`scipy~=1.12` / `numpy~=1.26`.

Useful environment variables (all optional):

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMBERSIGHT_CHECKPOINT_DB` | `/tmp/embersight.db` | LangGraph checkpoints (pending interrupts survive restarts) |
| `EMBERSIGHT_OVERPASS_URLS` | overpass-api.de, kumi.systems, private.coffee | Overpass instances, tried in order |
| `EMBERSIGHT_OVERPASS_CACHE` | `/tmp/embersight-overpass-cache` | On-disk Overpass cache (lets a repeat run work offline) |
| `EMBERSIGHT_OVERPASS_TTL_HOURS` | `6` | Age after which a cached Overpass answer is refreshed |
| `NWS_USER_AGENT` | generic | Required by api.weather.gov; put a contact address in it |
