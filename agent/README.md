# embersight-agent

The FastAPI + LangGraph agent service behind the EmberSight dashboard. See the
repository root `README.md` for the full quickstart and architecture.

```bash
uv sync --python 3.13            # runtime deps + pytest (dev group)
uv run uvicorn embersight_agent.main:app --port 8000
uv run pytest -q                 # offline unit tests, no API key or network
```

Every agent has a deterministic fallback, so the service runs without an
`ANTHROPIC_API_KEY`; the LLM only rewrites the narrative text.
