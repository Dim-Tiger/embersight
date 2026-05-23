"""Synthetic ignition seeder for demos.

If no real California fires are active during a demo, run this to inject a
synthetic incident in Los Padres NF that the orchestrator treats just like
a real CAL FIRE record.

    uv run python -m embersight_agent.tools.seed_demo
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def make_synthetic_incident() -> dict:
    return {
        "id": "synthetic-demo-001",
        "name": "Demo Ridge Fire",
        "lat": 34.7402,
        "lon": -119.3142,
        "acres": 142.0,
        "contained_pct": 0.0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": "synthetic",
        "raw": {
            "note": "Synthetic ignition seeded for demo day fallback.",
            "wui_density": "high",
        },
    }


if __name__ == "__main__":
    print(json.dumps(make_synthetic_incident(), indent=2))
