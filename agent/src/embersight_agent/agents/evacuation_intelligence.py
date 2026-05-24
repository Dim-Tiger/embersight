"""Evacuation Intelligence subagent.

Cross-references Cal OES `CA_EVACUATIONS` zones with the predicted spread
cone (Spread Simulation), values-at-risk population, and routing/staging
egress capacity to PROPOSE zone status changes (NORMAL → WARNING → ORDER).

**EmberSight never publishes evacuation orders.** For every proposed
change this agent fires `hitl.request_human_decision(...)` with an
`evac_zone_change` envelope. The human IC approves / edits / rejects each
proposal individually; the terminal action of this agent is the
interrupt request, never a publish/send/dispatch/order side effect.

Reads:
  state.incident
  state.outputs["spread_simulation"].payload["cones"]   (1/6/12/24h)
  state.outputs["values_at_risk"].payload               (population proxy)
  state.outputs["routing_staging"].payload              (road graph)

Writes:
  outputs["evacuation_intelligence"] : AgentOutput
      payload.zone_changes = [
          {zone_id, name, current_status, proposed_status,
           final_status, decision, rationale, population_estimate,
           egress_status}
      ]
  audit_log : list[InterruptRecord]   (one per proposed change)
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ..hitl import audit_entry, request_human_decision
from ..state import AgentOutput, AgentState, CitationBundle, Dataset, Model
from ..tools import evac as evac_tools
from ..tools import zone_catalog

log = logging.getLogger(__name__)

AGENT_NAME = "evacuation_intelligence"
LLM_MODEL = "claude-haiku-4-5"

# How far (in degrees, ~111 km/deg) to scan from the incident centre when
# pulling Cal OES zones. ~0.5 deg ≈ 55 km radius is plenty for a 24h
# spread horizon in extreme conditions.
DEFAULT_BBOX_HALF_DEG = 0.5

# How long the IC has to act on each proposed change before the envelope
# is considered stale. The graph stays paused regardless; this is purely
# advisory metadata for the UI.
DEFAULT_DECISION_TTL_MIN = 30


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _bbox_around_incident(
    incident: Any, half_deg: float = DEFAULT_BBOX_HALF_DEG
) -> tuple[float, float, float, float] | None:
    if incident is None:
        return None
    return (
        incident.lon - half_deg,
        incident.lat - half_deg,
        incident.lon + half_deg,
        incident.lat + half_deg,
    )


def _wkt_to_geojson(wkt: str) -> dict | None:
    """Best-effort WKT polygon → GeoJSON geometry. None on failure."""
    if not wkt:
        return None
    try:
        from shapely import wkt as _wkt  # noqa: PLC0415
        from shapely.geometry import mapping  # noqa: PLC0415

        geom = _wkt.loads(wkt)
        return mapping(geom)
    except Exception:  # noqa: BLE001
        return None


def _coerce_geom(value: Any):
    """Convert WKT / GeoJSON / shapely geometry to a shapely BaseGeometry,
    or None on failure. Lazy-imports shapely."""
    if value is None:
        return None
    try:
        from shapely import wkt as _wkt  # noqa: PLC0415
        from shapely.geometry import shape  # noqa: PLC0415
        from shapely.geometry.base import BaseGeometry  # noqa: PLC0415
    except ImportError:
        return None

    if isinstance(value, BaseGeometry):
        return value
    if isinstance(value, str):
        try:
            return _wkt.loads(value)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, dict):
        try:
            return shape(value)
        except Exception:  # noqa: BLE001
            return None
    return None


def _extract_primary_cone(spread_payload: Any) -> Any:
    """Resolve the canonical spread-cone polygon for phasing.

    Prefers a top-level ``polygon_wkt`` (forward-compatible with a future
    spread payload contract), then falls back to the widest available
    horizon in ``cones`` (24h → 12h → 6h → 1h).
    """
    if not isinstance(spread_payload, dict):
        return None
    wkt = spread_payload.get("polygon_wkt")
    if wkt:
        return wkt
    cones = spread_payload.get("cones") or {}
    for key in ("24h", "12h", "6h", "1h"):
        c = cones.get(key)
        if c is not None:
            return c
    return None


def _cone_radius_deg(cone_geom: Any, incident: Any) -> float:
    """Max distance (in degrees) from the incident point to the cone boundary.

    Used to define the WATCH band as a buffer of `2 × cone_radius` around
    the incident. Returns 0.0 if either input is unusable.
    """
    if cone_geom is None or incident is None:
        return 0.0
    try:
        from shapely.geometry import Point  # noqa: PLC0415

        ip = Point(incident.lon, incident.lat)
        # hausdorff_distance on the boundary gives the farthest cone point.
        return float(ip.hausdorff_distance(cone_geom))
    except Exception:  # noqa: BLE001
        return 0.0


def _phase_for_zone(
    zone_geom: Any,
    cone_geom: Any,
    egress: dict,
    incident: Any,
    cone_radius_deg: float,
) -> str | None:
    """Classify a zone into one of: 'order' | 'advisory' | 'watch' | None.

    - ORDER: a major road-graph egress edge crossing the zone boundary is
      blocked by the spread cone (per `compute_evacuation_routes_clear`).
    - ADVISORY (WARNING): the zone polygon touches/intersects the cone.
    - WATCH: zone sits within 2× the cone radius of the incident point but
      does not touch the cone itself.
    - None: zone is outside all bands.
    """
    if zone_geom is None:
        return None

    egress_blocked = (egress or {}).get("clear") is False and (
        (egress or {}).get("egress_edges_blocked", 0) > 0
    )
    touches_cone = False
    if cone_geom is not None:
        try:
            touches_cone = bool(zone_geom.intersects(cone_geom))
        except Exception:  # noqa: BLE001
            touches_cone = False

    if touches_cone and egress_blocked:
        return "order"
    if touches_cone:
        return "advisory"

    if incident is not None and cone_radius_deg > 0.0:
        try:
            from shapely.geometry import Point  # noqa: PLC0415

            ip = Point(incident.lon, incident.lat)
            if zone_geom.distance(ip) <= 2.0 * cone_radius_deg:
                return "watch"
        except Exception:  # noqa: BLE001
            return None
    return None


def _phase_entry(zone: dict, phase: str, egress: dict, population: int) -> dict:
    """Compact record for the phasing bucket payloads."""
    return {
        "zone_id": zone["zone_id"],
        "name": zone["name"],
        "current_status": zone["current_status"],
        "proposed_phase": phase.upper(),
        "jurisdiction": zone.get("jurisdiction"),
        "population_estimate": population,
        "egress_status": {
            "clear": egress.get("clear"),
            "reason": egress.get("reason"),
            "egress_edges_blocked": egress.get("egress_edges_blocked", 0),
            "egress_edges_checked": egress.get("egress_edges_checked", 0),
        },
    }


def _overlap_fraction(zone_geom: Any, cone_geom: Any) -> float:
    """Fraction of the zone polygon covered by the cone, in [0, 1].

    Returns 0.0 if either geometry is missing or invalid.
    """
    if zone_geom is None or cone_geom is None:
        return 0.0
    try:
        if not zone_geom.is_valid or not cone_geom.is_valid:
            return 0.0
        z_area = zone_geom.area
        if z_area <= 0:
            return 0.0
        inter = zone_geom.intersection(cone_geom).area
        return max(0.0, min(1.0, inter / z_area))
    except Exception:  # noqa: BLE001
        return 0.0


def _deterministic_proposal(
    zone: dict, overlaps: dict, egress: dict
) -> tuple[str, str]:
    """Deterministic fallback proposal logic (no LLM).

    Rules:
      NORMAL  → WARNING if 12h overlap ≥ 10% (or 24h overlap ≥ 30%).
      NORMAL  → ORDER   if 6h  overlap ≥ 50% OR egress is blocked.
      WARNING → ORDER   if 6h  overlap ≥ 50% OR egress is blocked.
      WARNING → NORMAL  is not auto-proposed (only manual lift).
      ORDER   → stays   (lifting an order is a manual decision).
    """
    current = zone["current_status"]
    o6 = overlaps.get("6h", 0.0)
    o12 = overlaps.get("12h", 0.0)
    o24 = overlaps.get("24h", 0.0)
    egress_clear = egress.get("clear")

    egress_blocked = egress_clear is False
    in_6h_50 = o6 >= 0.5
    in_12h_10 = o12 >= 0.1
    in_24h_30 = o24 >= 0.3

    if current == "ORDER":
        return (
            "ORDER",
            "Zone already under ORDER — maintained (lifting is a manual IC call).",
        )

    if in_6h_50 or egress_blocked:
        rationale_parts = []
        if in_6h_50:
            rationale_parts.append(f"6h spread cone covers {o6:.0%} of zone")
        if egress_blocked:
            rationale_parts.append(
                f"egress at risk: {egress.get('reason', 'blocked')}"
            )
        return "ORDER", "; ".join(rationale_parts)

    if current == "NORMAL" and (in_12h_10 or in_24h_30):
        return (
            "WARNING",
            f"12h cone covers {o12:.0%} / 24h cone covers {o24:.0%} of zone",
        )

    if current == "WARNING":
        return (
            "WARNING",
            f"Maintained — 6h overlap {o6:.0%} below ORDER threshold",
        )

    return ("NORMAL", "No meaningful overlap with predicted spread cone.")


def _load_evac_system_prompt() -> str:
    try:
        from pathlib import Path  # noqa: PLC0415

        return (
            Path(__file__).resolve().parent.parent
            / "prompts"
            / "evacuation_intelligence.md"
        ).read_text(encoding="utf-8")
    except OSError:
        return (
            "You PROPOSE Cal OES evacuation zone status changes. Verbs are "
            "PROPOSE / RECOMMEND only. Never issue / publish / order."
        )


def _build_zone_prompt(zone: dict, overlaps: dict, population: int, egress: dict) -> str:
    return (
        "Zone details:\n"
        f"- id: {zone['zone_id']}\n"
        f"- name: {zone['name']}\n"
        f"- current_status: {zone['current_status']}\n"
        f"- jurisdiction: {zone.get('jurisdiction', 'unknown')}\n"
        f"- population_estimate: {population}\n"
        f"- spread cone overlap (fraction of zone): "
        f"6h={overlaps.get('6h', 0.0):.2f}, "
        f"12h={overlaps.get('12h', 0.0):.2f}, "
        f"24h={overlaps.get('24h', 0.0):.2f}\n"
        f"- egress: clear={egress.get('clear')}, "
        f"reason={egress.get('reason')}\n\n"
        "Reply with EXACTLY two lines:\n"
        "PROPOSED_STATUS: <NORMAL|WARNING|ORDER>\n"
        "RATIONALE: <one sentence>"
    )


def _parse_llm_text(text: str) -> tuple[str, str] | None:
    proposed = None
    rationale = None
    for line in str(text).splitlines():
        s = line.strip()
        if s.upper().startswith("PROPOSED_STATUS:"):
            value = s.split(":", 1)[1].strip().upper()
            if value in {"NORMAL", "WARNING", "ORDER"}:
                proposed = value
        elif s.upper().startswith("RATIONALE:"):
            rationale = s.split(":", 1)[1].strip()
    if proposed is None:
        return None
    return proposed, rationale or "(no rationale returned by model)"


async def _llm_proposal_async(
    zone: dict,
    overlaps: dict,
    population: int,
    egress: dict,
) -> tuple[str, str] | None:
    """Ask Claude Haiku 4.5 for a proposed status + rationale (async).

    Returns ``(proposed_status, rationale)`` or ``None`` if the LLM call
    fails / the model output cannot be parsed. The caller falls back to
    the deterministic proposal.

    This MUST stay async + use ``ainvoke``. Each LLM call takes ~2-3s
    against Anthropic; running them sync inside an ``async def`` blocks
    the uvicorn event loop for the duration, which freezes every other
    in-flight request (SSE streams, healthz, etc) for the cumulative
    LLM time. With our cap of 20 calls per briefing that's a 60s
    server-wide freeze. Asyncified + gathered by the caller, the same
    20 calls finish in ~3s wall-time without blocking.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    try:
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
    except ImportError:
        return None

    system_prompt = _load_evac_system_prompt()
    user_msg = _build_zone_prompt(zone, overlaps, population, egress)

    try:
        chat = ChatAnthropic(model=LLM_MODEL, max_tokens=200, temperature=0.0)
        resp = await chat.ainvoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
        )
        text = getattr(resp, "content", "") or ""
        if isinstance(text, list):
            # langchain message content can be a list of parts
            text = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in text
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM proposal failed for zone %s: %s", zone["zone_id"], exc)
        return None

    return _parse_llm_text(text)


def _llm_proposal(
    zone: dict,
    overlaps: dict,
    population: int,
    egress: dict,
) -> tuple[str, str] | None:
    """DEPRECATED sync wrapper kept only for backwards-compat tests.

    In the async ``run()`` path we use ``_llm_proposal_async`` and
    ``asyncio.gather`` so 20 LLM calls finish in ~3s rather than 60s
    of blocked event loop. Calling this sync version from inside the
    async graph node is a perf regression — do not.
    """
    import asyncio  # noqa: PLC0415

    try:
        return asyncio.run(
            _llm_proposal_async(zone, overlaps, population, egress)
        )
    except RuntimeError:
        # Already inside a running loop — fall back to deterministic.
        return None


def _confidence(
    have_spread: bool,
    have_values: bool,
    have_routing: bool,
    zones_fetched: int,
    fetch_errors: int,
) -> tuple[float, str]:
    """Compute confidence + driver string.

    1.0 if all upstream data present, no fetch errors, and at least one
    zone returned. Degrade by 0.2 per missing upstream output and by 0.3
    per error.
    """
    score = 1.0
    drivers = []
    if not have_spread:
        score -= 0.3
        drivers.append("spread_simulation output missing")
    if not have_values:
        score -= 0.15
        drivers.append("values_at_risk output missing")
    if not have_routing:
        score -= 0.15
        drivers.append("routing_staging output missing")
    if fetch_errors:
        score -= 0.3
        drivers.append(f"{fetch_errors} fetch error(s)")
    if zones_fetched == 0:
        score -= 0.2
        drivers.append("no Cal OES zones returned for incident bbox")
    score = max(0.0, min(1.0, score))
    driver = "; ".join(drivers) if drivers else "all upstream inputs present"
    return score, driver


# --------------------------------------------------------------------------- #
# IC-directed test-mode synthesizer
# --------------------------------------------------------------------------- #


async def _run_synthetic_test_mode(
    state: AgentState, instruction_raw: str
) -> dict:
    """Fire a WARNING and an ORDER synthetic evac_zone_change interrupt
    pair, near the current incident, with rationale_source="synthetic_test".

    Invoked when the IC consults evac_intel with an instruction that
    looks like a system test ("make a test zone", "demo the queue",
    "smoke-test evacuations"). Bypasses the catalog/cone pipeline so
    the IC can exercise the human-in-the-loop path on any incident,
    even ones with no real Zonehaven coverage or no spread cone yet.

    Returns the same shape as the normal run(): outputs + audit_log
    patch. The two interrupts fire sequentially (LangGraph pauses the
    graph between them), so the human sees them appear one at a time
    in the approval queue.
    """
    incident = state.incident
    if incident is None:
        # Nothing to anchor synthetic polygons to.
        out = AgentOutput(
            agent=AGENT_NAME,
            narrative=(
                "Cannot generate synthetic test proposals — no incident "
                "selected. RECOMMEND selecting a fire first."
            ),
            payload={
                "zone_changes": [],
                "synthetic_test": True,
                "instruction": instruction_raw,
            },
            confidence=0.0,
            confidence_driver="no incident in state",
        )
        return {"outputs": {AGENT_NAME: out}}

    audit_records: list[Any] = []
    zone_changes: list[dict] = []
    counts = {"proposed": 0, "approved": 0, "edited": 0, "rejected": 0}

    def _square_polygon_wkt(cx: float, cy: float, half: float) -> str:
        ring = ", ".join(
            f"{x} {y}"
            for x, y in [
                (cx - half, cy - half),
                (cx + half, cy - half),
                (cx + half, cy + half),
                (cx - half, cy + half),
                (cx - half, cy - half),
            ]
        )
        return f"POLYGON (({ring}))"

    # Two synthetic zones: ORDER northeast, WARNING northwest of the fire.
    plan = [
        {
            "status": "ORDER",
            "label": "Test Order Zone (synthetic)",
            "offset": (0.015, 0.012),
            "half": 0.012,  # ~1.3 km square
            "pop": 1850,
            "structures": 740,
            "egress_clear": False,
            "egress_blocked_edges": 2,
            "why": [
                "Synthetic test ORDER — IC requested a demo proposal.",
                "Placed ~1.5 km NE of incident centroid; egress modeled at-risk.",
                "Exercises the full approval queue + map overlay path.",
            ],
        },
        {
            "status": "WARNING",
            "label": "Test Warning Zone (synthetic)",
            "offset": (-0.018, 0.014),
            "half": 0.013,
            "pop": 920,
            "structures": 365,
            "egress_clear": True,
            "egress_blocked_edges": 0,
            "why": [
                "Synthetic test WARNING — IC requested a demo proposal.",
                "Placed ~1.8 km NW of incident centroid; egress modeled clear.",
                "Exercises the WARNING approval card and map overlay.",
            ],
        },
    ]

    ts = int(datetime.now(timezone.utc).timestamp())
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=DEFAULT_DECISION_TTL_MIN)
    ).isoformat()

    for i, z in enumerate(plan):
        dx, dy = z["offset"]
        cx, cy = incident.lon + dx, incident.lat + dy
        wkt = _square_polygon_wkt(cx, cy, z["half"])
        polygon_geojson = _wkt_to_geojson(wkt)
        zone_id = f"TEST-{z['status']}-{ts}-{i}"
        envelope = {
            "type": "evac_zone_change",
            "zone_id": zone_id,
            "name": f"{z['label']} (near {incident.name})",
            "jurisdiction": "Synthetic (test)",
            "current_status": "NORMAL",
            "proposed_status": z["status"],
            "rationale": z["why"][0],
            "rationale_source": "synthetic_test",
            "why": z["why"],
            "impact": {
                "human_displacement_estimate": z["pop"],
                "residential_structures_estimate": z["structures"],
                "egress_clear": z["egress_clear"],
                "egress_blocked_edges": z["egress_blocked_edges"],
            },
            "polygon_geojson": polygon_geojson,
            "population_estimate": z["pop"],
            "expires_at": expires_at,
        }
        counts["proposed"] += 1
        decision = request_human_decision("evac_zone_change", envelope)
        audit_records.append(audit_entry("evac_zone_change", envelope, decision))

        decision_kind = (decision or {}).get("decision", "rejected")
        edits = (decision or {}).get("edits") or {}
        edited_status = edits.get("proposed_status") if isinstance(edits, dict) else None
        if decision_kind == "approved":
            final_status = z["status"]
            counts["approved"] += 1
        elif decision_kind == "edited":
            final_status = (
                edited_status
                if edited_status in {"NORMAL", "WARNING", "ORDER"}
                else z["status"]
            )
            counts["edited"] += 1
        else:
            final_status = "NORMAL"
            counts["rejected"] += 1

        zone_changes.append(
            {
                "zone_id": zone_id,
                "name": envelope["name"],
                "current_status": "NORMAL",
                "proposed_status": z["status"],
                "final_status": final_status,
                "decision": decision,
                "rationale": z["why"][0],
                "rationale_source": "synthetic_test",
                "population_estimate": z["pop"],
                "egress_status": {
                    "clear": z["egress_clear"],
                    "egress_blocked_edges": z["egress_blocked_edges"],
                    "reason": "synthetic",
                },
                "overlaps": {"6h": 0.0, "12h": 0.0, "24h": 0.0},
            }
        )

    narrative = (
        f"Synthetic test proposals fired: "
        f"{counts['proposed']} PROPOSED, "
        f"{counts['approved']} approved, "
        f"{counts['edited']} edited, "
        f"{counts['rejected']} rejected. "
        f"Bypassed catalog and cone — these are demo polygons anchored "
        f"to the incident centroid, marked rationale_source=synthetic_test."
    )

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=narrative,
        payload={
            "zone_changes": zone_changes,
            "zones_order": [],
            "zones_advisory": [],
            "zones_watch": [],
            "zones_fetched": 0,
            "counts": counts,
            "synthetic_test": True,
            "instruction": instruction_raw,
        },
        confidence=1.0,
        confidence_driver="synthetic test mode (IC-directed)",
        citation_bundle=CitationBundle(
            datasets=[
                Dataset(
                    name="Synthetic test polygon",
                    version="anchored to incident centroid",
                ),
            ],
            models=[],
            reasoning_trace_id=str(uuid.uuid4()),
        ),
    )

    patch: dict[str, Any] = {"outputs": {AGENT_NAME: output}}
    if audit_records:
        patch["audit_log"] = audit_records
    return patch


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #


async def run(state: AgentState) -> dict:  # noqa: C901 — orchestration is naturally branchy
    incident = state.incident
    fetch_errors = 0

    # ---- IC-directed instruction (chat-mode dispatch) ------------------ #
    #
    # The Master IC can pass a plain-language instruction to us via
    # `state.scratch.consult_instructions["evacuation_intelligence"]`.
    # We read it and branch:
    #   - "test"/"demo"/"synthetic"  → fire a WARNING and an ORDER
    #     synthetic proposal regardless of catalog/cone state. Useful
    #     for system checks where the IC wants to exercise the queue,
    #     map overlay, and approval path end-to-end without waiting
    #     for real data.
    #   - everything else            → run the normal catalog+cone flow
    #     below. (Future: refinement instructions like "shrink the order
    #     zone 200m south" will branch here too.)
    instruction_raw = (
        (state.scratch or {}).get("consult_instructions") or {}
    ).get(AGENT_NAME, "") or ""
    instruction = instruction_raw.strip().lower()
    if instruction and any(
        kw in instruction
        for kw in ("test", "demo", "synthetic", "system check", "smoke", "exercise")
    ):
        log.info(
            "evac_intel: handling IC test-mode instruction %r → "
            "firing synthetic WARNING + ORDER proposals",
            instruction_raw[:120],
        )
        return await _run_synthetic_test_mode(state, instruction_raw)

    # ---- Upstream output extraction ----------------------------------- #
    spread = state.outputs.get("spread_simulation")
    values = state.outputs.get("values_at_risk")
    routing = state.outputs.get("routing_staging")
    have_spread = spread is not None
    have_values = values is not None
    have_routing = routing is not None

    spread_payload = spread.payload if spread else {}
    cones = spread_payload.get("cones", {}) if isinstance(spread_payload, dict) else {}
    cone_6h = _coerce_geom(cones.get("6h"))
    cone_12h = _coerce_geom(cones.get("12h"))
    cone_24h = _coerce_geom(cones.get("24h"))

    routing_payload = routing.payload if routing else {}
    road_graph = (
        routing_payload.get("road_graph")
        if isinstance(routing_payload, dict)
        else None
    )

    # Primary cone for phasing (prefer payload.polygon_wkt; fall back to
    # the widest available horizon under payload.cones).
    primary_cone_obj = _extract_primary_cone(spread_payload)
    primary_cone_geom = _coerce_geom(primary_cone_obj)
    cone_radius = _cone_radius_deg(primary_cone_geom, incident)

    # ---- Static zone catalog + live status overlay -------------------- #
    #
    # We need two things:
    #   1. The *static* universe of evac zones in the AOI — i.e. every
    #      zone defined by the local county, regardless of whether it's
    #      currently active. ``zone_catalog`` aggregates this from the
    #      Genasys WFS endpoints counties publish on ArcGIS Hub plus a
    #      handful of county-hosted Feature Services.
    #   2. A *live overlay* of which of those zones currently have an
    #      open WARNING or ORDER. Cal OES's aggregation layer is the
    #      canonical source (updated every 10 min) and is consulted via
    #      ``evac_tools.get_active_status_overlay``.
    #
    # The agent then overlays (2) onto (1) so ``zone.current_status`` is
    # ground truth before we ask the LLM/rule to propose any change.
    bbox = _bbox_around_incident(incident)
    zones: list[dict] = []
    if bbox is not None:
        try:
            zones = await zone_catalog.fetch_static_zones_in_bbox(bbox)
        except Exception as exc:  # noqa: BLE001
            log.warning("zone_catalog.fetch_static_zones_in_bbox raised: %s", exc)
            fetch_errors += 1
            zones = []

        # If the static catalog has nothing for this AOI (e.g. the
        # incident is on an offshore island or in a county that hasn't
        # published its Zonehaven authkey yet), fall back to the legacy
        # Cal OES active-only fetch so the agent has *something* to
        # reason about rather than going silent.
        if not zones:
            try:
                zones = await evac_tools.get_calevacs_zones(bbox)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "get_calevacs_zones fallback raised: %s", exc
                )
                fetch_errors += 1
                zones = []

        # Live status overlay — overrides static `current_status` (always
        # NORMAL coming out of the catalog) with the real active status
        # for any zone Cal OES considers in warning/order right now.
        if zones:
            try:
                overlay = await evac_tools.get_active_status_overlay(bbox)
            except Exception as exc:  # noqa: BLE001
                log.warning("get_active_status_overlay raised: %s", exc)
                overlay = {}
            if overlay:
                for z in zones:
                    k_id, k_name = evac_tools.overlay_key_for_zone(z)
                    live = overlay.get(k_id) or overlay.get(k_name)
                    if live:
                        z["current_status"] = live

    # ---- Per-zone analysis + interrupts ------------------------------- #
    zone_changes: list[dict] = []
    zones_order: list[dict] = []
    zones_advisory: list[dict] = []
    zones_watch: list[dict] = []
    audit_records = []
    key_findings: list[str] = []
    counts = {"proposed": 0, "approved": 0, "edited": 0, "rejected": 0}

    active_evac_geoms = [
        _coerce_geom(z["polygon_wkt"])
        for z in zones
        if z["current_status"] == "ORDER"
    ]
    active_evac_geoms = [g for g in active_evac_geoms if g is not None]

    # ---- Pre-filter: shortlist zones worth examining --------------------- #
    #
    # The statewide static catalog routinely returns >1,000 zones in a
    # half-degree AOI. Looping every zone through the per-zone LLM call
    # would block the briefing for tens of minutes. A zone only deserves
    # the LLM treatment if it could plausibly change status — i.e. it
    # already overlaps the cone, sits adjacent to an active ORDER zone,
    # or is itself currently active.
    #
    # All other zones are coalesced into a single "status maintained"
    # bucket (cheap, deterministic). Maintained-NORMAL zones are not
    # individually recorded in zone_changes — that's just noise; the
    # agent's audience cares about the proposed *changes*, not the 950+
    # zones that stayed put.
    #
    # `LLM_CALL_CAP` is a final safety belt: even after the geometry
    # shortlist, we never fire more than this many Haiku calls per
    # briefing. Sorted by 12-hr overlap so the most relevant zones win.
    LLM_CALL_CAP = int(os.environ.get("EMBERSIGHT_EVAC_LLM_CAP", "20"))

    candidates: list[dict] = []
    for zone in zones:
        zone_geom = _coerce_geom(zone["polygon_wkt"])
        ov_6h = _overlap_fraction(zone_geom, cone_6h)
        ov_12h = _overlap_fraction(zone_geom, cone_12h)
        ov_24h = _overlap_fraction(zone_geom, cone_24h)

        adjacent_to_order = False
        if zone_geom is not None and active_evac_geoms:
            try:
                buffered = zone_geom.buffer(0.0025)  # ~250 m at CA lats
                adjacent_to_order = any(
                    buffered.intersects(g) for g in active_evac_geoms
                )
            except Exception:  # noqa: BLE001
                adjacent_to_order = False

        worth_examining = (
            ov_24h > 0.0
            or adjacent_to_order
            or zone["current_status"] != "NORMAL"
        )
        if not worth_examining:
            continue

        candidates.append(
            {
                "zone": zone,
                "zone_geom": zone_geom,
                "overlaps": {"6h": ov_6h, "12h": ov_12h, "24h": ov_24h},
                "adjacent_to_order": adjacent_to_order,
            }
        )

    # Most-relevant-first so the LLM cap chooses well.
    candidates.sort(
        key=lambda c: (
            c["overlaps"]["6h"],
            c["overlaps"]["12h"],
            c["overlaps"]["24h"],
            int(c["adjacent_to_order"]),
        ),
        reverse=True,
    )
    skipped_zone_count = len(zones) - len(candidates)
    log.info(
        "evac_intel: %d/%d zones shortlisted for analysis (%d skipped as "
        "untouched-NORMAL). LLM cap=%d.",
        len(candidates), len(zones), skipped_zone_count, LLM_CALL_CAP,
    )

    # ---- Pre-compute per-candidate features (no LLM yet) ----------------- #
    #
    # The expensive thing is the LLM call. We do it in a single parallel
    # `asyncio.gather` after this pre-pass, then walk candidates a second
    # time with the LLM results pinned to each.
    import asyncio as _asyncio  # noqa: PLC0415

    enriched: list[dict] = []
    llm_targets: list[int] = []  # indices into `enriched` that get an LLM call
    for cand in candidates:
        zone = cand["zone"]
        zone_geom = cand["zone_geom"]
        overlaps = cand["overlaps"]
        adjacent_to_order = cand["adjacent_to_order"]

        population = evac_tools.estimate_population(zone["polygon_wkt"])
        egress = evac_tools.compute_evacuation_routes_clear(
            zone["polygon_wkt"],
            road_graph,
            spread_cone=cones.get("6h") or primary_cone_obj,
        )

        phase = _phase_for_zone(
            zone_geom, primary_cone_geom, egress, incident, cone_radius
        )
        if phase == "order":
            zones_order.append(_phase_entry(zone, phase, egress, population))
        elif phase == "advisory":
            zones_advisory.append(_phase_entry(zone, phase, egress, population))
        elif phase == "watch":
            zones_watch.append(_phase_entry(zone, phase, egress, population))

        effective_overlaps = dict(overlaps)
        if adjacent_to_order and effective_overlaps["12h"] < 0.10:
            effective_overlaps["12h"] = 0.10

        # LLM gate — same logic as before, just decided up-front so we
        # can dispatch the calls in a single gather.
        use_llm = (
            len(llm_targets) < LLM_CALL_CAP
            and (
                effective_overlaps["6h"] >= 0.02
                or effective_overlaps["12h"] >= 0.05
                or adjacent_to_order
                or zone["current_status"] != "NORMAL"
            )
        )
        idx = len(enriched)
        enriched.append(
            {
                "zone": zone,
                "zone_geom": zone_geom,
                "overlaps": overlaps,
                "effective_overlaps": effective_overlaps,
                "adjacent_to_order": adjacent_to_order,
                "population": population,
                "egress": egress,
                "use_llm": use_llm,
            }
        )
        if use_llm:
            llm_targets.append(idx)

    # ---- Fan out every LLM call concurrently ----------------------------- #
    #
    # Each call takes ~2-3s against Anthropic; serial they cost up to
    # LLM_CALL_CAP × 3s = 60s of blocked event loop. Parallel they
    # finish in roughly one call's worth of wall-time (~3s) and don't
    # block other uvicorn requests in between (healthz, SSE, etc.).
    if llm_targets:
        llm_results = await _asyncio.gather(
            *(
                _llm_proposal_async(
                    enriched[i]["zone"],
                    enriched[i]["effective_overlaps"],
                    enriched[i]["population"],
                    enriched[i]["egress"],
                )
                for i in llm_targets
            ),
            return_exceptions=True,
        )
        for i, res in zip(llm_targets, llm_results):
            enriched[i]["llm_out"] = (
                res if not isinstance(res, BaseException) else None
            )
    llm_calls = sum(
        1 for e in enriched if e["use_llm"] and e.get("llm_out") is not None
    )

    # ---- Per-zone proposal + interrupt loop ------------------------------- #
    for cand in enriched:
        zone = cand["zone"]
        overlaps = cand["overlaps"]
        effective_overlaps = cand["effective_overlaps"]
        adjacent_to_order = cand["adjacent_to_order"]
        population = cand["population"]
        egress = cand["egress"]
        llm_out = cand.get("llm_out") if cand.get("use_llm") else None

        if llm_out is not None:
            proposed_status, rationale = llm_out
            rationale_source = "llm"
        else:
            proposed_status, rationale = _deterministic_proposal(
                zone, effective_overlaps, egress
            )
            rationale_source = "deterministic"
            if adjacent_to_order and proposed_status == "NORMAL":
                proposed_status = "WARNING"
                rationale = (
                    "Adjacent to existing ORDER zone — proposing WARNING."
                )

        if proposed_status == zone["current_status"]:
            key_findings.append(
                f"{zone['zone_id']} ({zone['name']}): status maintained "
                f"({proposed_status}). {rationale}"
            )
            zone_changes.append(
                {
                    "zone_id": zone["zone_id"],
                    "name": zone["name"],
                    "current_status": zone["current_status"],
                    "proposed_status": proposed_status,
                    "final_status": proposed_status,
                    "decision": {"decision": "maintained", "edits": None},
                    "rationale": rationale,
                    "rationale_source": rationale_source,
                    "population_estimate": population,
                    "egress_status": egress,
                    "overlaps": overlaps,
                }
            )
            continue

        # Interrupt: ask the IC to approve / edit / reject this change.
        counts["proposed"] += 1
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(minutes=DEFAULT_DECISION_TTL_MIN)
        ).isoformat()

        polygon_geojson = _wkt_to_geojson(zone["polygon_wkt"])
        residential_count = 0
        try:
            residential_count = max(
                0, int(round(population / DEFAULT_HOUSEHOLD_SIZE))
            )
        except Exception:  # noqa: BLE001
            residential_count = 0

        # Structured "why" — assembled from the model's one-liner + the
        # signals that drove the proposal. The IC card formats these as
        # bullets so the human can see the reasoning, not just the answer.
        why_bullets: list[str] = []
        if rationale:
            why_bullets.append(rationale)
        if overlaps.get("6h", 0) > 0:
            why_bullets.append(
                f"6-hr spread cone covers {overlaps['6h']:.0%} of zone area"
            )
        if overlaps.get("12h", 0) > 0:
            why_bullets.append(
                f"12-hr cone covers {overlaps['12h']:.0%}"
            )
        if overlaps.get("24h", 0) > 0:
            why_bullets.append(
                f"24-hr cone covers {overlaps['24h']:.0%}"
            )
        if egress.get("clear") is False:
            why_bullets.append(
                f"Egress at risk — {egress.get('reason', 'route blocked')}"
            )
        elif egress.get("clear") is True:
            why_bullets.append("Egress routes currently clear")
        if adjacent_to_order:
            why_bullets.append("Borders an existing ORDER zone")

        impact = {
            "human_displacement_estimate": population,
            "residential_structures_estimate": residential_count,
            "egress_clear": egress.get("clear"),
            "egress_blocked_edges": egress.get("egress_edges_blocked", 0),
        }

        envelope = {
            "type": "evac_zone_change",
            "zone_id": zone["zone_id"],
            "name": zone["name"],
            "jurisdiction": zone.get("jurisdiction"),
            "current_status": zone["current_status"],
            "proposed_status": proposed_status,
            "rationale": rationale,
            "rationale_source": rationale_source,
            "why": why_bullets,
            "impact": impact,
            "polygon_geojson": polygon_geojson,
            "population_estimate": population,
            "egress_status": egress,
            "overlaps": overlaps,
            "adjacent_to_active_evac_zone": adjacent_to_order,
            "expires_at": expires_at,
        }
        decision = request_human_decision("evac_zone_change", envelope)
        audit_records.append(audit_entry("evac_zone_change", envelope, decision))

        decision_kind = (decision or {}).get("decision", "rejected")
        edits = (decision or {}).get("edits") or {}
        edited_status = edits.get("proposed_status") if isinstance(edits, dict) else None

        if decision_kind == "approved":
            final_status = proposed_status
            counts["approved"] += 1
        elif decision_kind == "edited":
            final_status = (
                edited_status
                if edited_status in {"NORMAL", "WARNING", "ORDER"}
                else proposed_status
            )
            counts["edited"] += 1
        else:
            final_status = zone["current_status"]
            counts["rejected"] += 1

        zone_changes.append(
            {
                "zone_id": zone["zone_id"],
                "name": zone["name"],
                "current_status": zone["current_status"],
                "proposed_status": proposed_status,
                "final_status": final_status,
                "decision": decision,
                "rationale": rationale,
                "rationale_source": rationale_source,
                "population_estimate": population,
                "egress_status": egress,
                "overlaps": overlaps,
            }
        )
        key_findings.append(
            f"{zone['zone_id']} ({zone['name']}): "
            f"{zone['current_status']} → PROPOSED {proposed_status} "
            f"[{decision_kind}]; pop≈{population}; {rationale}"
        )

    # ---- Confidence + AgentOutput ------------------------------------- #
    confidence, confidence_driver = _confidence(
        have_spread, have_values, have_routing, len(zones), fetch_errors
    )

    citation_bundle = CitationBundle(
        datasets=[
            Dataset(
                name="Cal OES CA_EVACUATIONS",
                version=evac_tools.CALEVACS_VERSION,
                url=evac_tools.CALEVACS_ENDPOINT,
            ),
            Dataset(
                name="spread_simulation cones (1/6/12/24h)",
                version=(spread.payload.get("model_version", "stub") if spread else "missing"),
            ),
            Dataset(
                name="Microsoft Building Footprints (population proxy)",
                version="2024",
                url="https://github.com/microsoft/USBuildingFootprints",
            ),
        ],
        models=[Model(name=LLM_MODEL, version="haiku-4.5")],
        reasoning_trace_id=str(uuid.uuid4()),
    )

    summary = (
        f"{counts['proposed']} zone changes PROPOSED "
        f"({len(candidates)}/{len(zones)} zones reviewed; "
        f"{llm_calls} LLM call{'s' if llm_calls != 1 else ''})"
    )

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=summary,
        payload={
            "zone_changes": zone_changes,
            "zones_order": zones_order,
            "zones_advisory": zones_advisory,
            "zones_watch": zones_watch,
            "zones_fetched": len(zones),
            "zones_reviewed": len(candidates),
            "zones_skipped_untouched": skipped_zone_count,
            "llm_calls": llm_calls,
            "llm_call_cap": LLM_CALL_CAP,
            "counts": counts,
            "bbox": bbox,
            "cone_radius_deg": cone_radius,
        },
        confidence=confidence,
        confidence_driver=confidence_driver,
        citation_bundle=citation_bundle,
    )

    patch: dict[str, Any] = {"outputs": {AGENT_NAME: output}}
    if audit_records:
        patch["audit_log"] = audit_records
    return patch


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #


def _smoke_run() -> None:
    """Run the agent against a mocked state and print the result patch."""
    import asyncio
    import sys

    # Wire up mock zones, spread cones, road graph, and HITL decisions.
    # Mock zones — two simple square polygons offset from the incident.
    incident_lat, incident_lon = 38.500, -121.500

    zone_a_wkt = (
        "POLYGON (("
        f"{incident_lon - 0.005} {incident_lat - 0.005}, "
        f"{incident_lon + 0.005} {incident_lat - 0.005}, "
        f"{incident_lon + 0.005} {incident_lat + 0.005}, "
        f"{incident_lon - 0.005} {incident_lat + 0.005}, "
        f"{incident_lon - 0.005} {incident_lat - 0.005}"
        "))"
    )
    zone_b_wkt = (
        "POLYGON (("
        f"{incident_lon + 0.03} {incident_lat + 0.03}, "
        f"{incident_lon + 0.04} {incident_lat + 0.03}, "
        f"{incident_lon + 0.04} {incident_lat + 0.04}, "
        f"{incident_lon + 0.03} {incident_lat + 0.04}, "
        f"{incident_lon + 0.03} {incident_lat + 0.03}"
        "))"
    )
    # Z3 sits outside the 24h cone (radius 0.10) but inside the 2×-cone
    # WATCH band (radius 0.20).
    zone_c_wkt = (
        "POLYGON (("
        f"{incident_lon + 0.13} {incident_lat + 0.13}, "
        f"{incident_lon + 0.14} {incident_lat + 0.13}, "
        f"{incident_lon + 0.14} {incident_lat + 0.14}, "
        f"{incident_lon + 0.13} {incident_lat + 0.14}, "
        f"{incident_lon + 0.13} {incident_lat + 0.13}"
        "))"
    )

    async def fake_get_zones(bbox):  # noqa: ARG001
        return [
            {
                "zone_id": "Z1",
                "name": "Mock Zone A (near incident)",
                "current_status": "NORMAL",
                "polygon_wkt": zone_a_wkt,
                "last_updated_iso": "2026-05-23T00:00:00Z",
                "jurisdiction": "Mock County",
            },
            {
                "zone_id": "Z2",
                "name": "Mock Zone B (further out)",
                "current_status": "NORMAL",
                "polygon_wkt": zone_b_wkt,
                "last_updated_iso": "2026-05-23T00:00:00Z",
                "jurisdiction": "Mock County",
            },
            {
                "zone_id": "Z3",
                "name": "Mock Zone C (watch band)",
                "current_status": "NORMAL",
                "polygon_wkt": zone_c_wkt,
                "last_updated_iso": "2026-05-23T00:00:00Z",
                "jurisdiction": "Mock County",
            },
        ]

    # Spread cones: 6h cone is a small disk centered on the incident
    # (covers Z1 but not Z2); 12h cone is a larger disk (covers both);
    # 24h is larger still. WKT polygons (octagons approximating disks).
    def disk_wkt(cx: float, cy: float, r: float, n: int = 16) -> str:
        import math

        pts = [
            (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        pts.append(pts[0])
        ring = ", ".join(f"{x} {y}" for x, y in pts)
        return f"POLYGON (({ring}))"

    cone_6h_wkt = disk_wkt(incident_lon, incident_lat, 0.01)
    cone_12h_wkt = disk_wkt(incident_lon, incident_lat, 0.05)
    cone_24h_wkt = disk_wkt(incident_lon, incident_lat, 0.10)

    from .. import hitl as hitl_module
    from ..state import AgentOutput, AgentState, CitationBundle, Incident

    # Pre-populate upstream outputs.
    spread_out = AgentOutput(
        agent="spread_simulation",
        narrative="mock spread cones",
        payload={
            "cones": {
                "1h": None,
                "6h": cone_6h_wkt,
                "12h": cone_12h_wkt,
                "24h": cone_24h_wkt,
            },
            "model_version": "smoke-0",
        },
        confidence=0.7,
        confidence_driver="mock",
        citation_bundle=CitationBundle(),
    )
    values_out = AgentOutput(
        agent="values_at_risk",
        narrative="mock values",
        payload={"population_proxy": "mock"},
        confidence=0.7,
        confidence_driver="mock",
        citation_bundle=CitationBundle(),
    )
    # Build a tiny networkx road graph with one PRIMARY edge that crosses
    # Z1's boundary and intersects the 6h spread cone — drives Z1 into
    # the ORDER bucket.
    import networkx as nx
    from shapely.geometry import LineString

    rg = nx.MultiDiGraph()
    rg.add_node(1, x=incident_lon - 0.010, y=incident_lat)
    rg.add_node(2, x=incident_lon + 0.010, y=incident_lat)
    rg.add_edge(
        1,
        2,
        highway="primary",
        geometry=LineString(
            [
                (incident_lon - 0.010, incident_lat),
                (incident_lon + 0.010, incident_lat),
            ]
        ),
    )
    routing_out = AgentOutput(
        agent="routing_staging",
        narrative="mock routing (single primary egress edge)",
        payload={"road_graph": rg},
        confidence=0.7,
        confidence_driver="mock",
        citation_bundle=CitationBundle(),
    )

    state = AgentState(
        incident=Incident(
            id="SMOKE-1",
            name="SmokeFire",
            lat=incident_lat,
            lon=incident_lon,
            source="synthetic",
        ),
        operational_period=1,
        user_query="smoke",
        outputs={
            "spread_simulation": spread_out,
            "values_at_risk": values_out,
            "routing_staging": routing_out,
        },
    )

    # Stub the network call.
    evac_tools.get_calevacs_zones = fake_get_zones  # type: ignore[assignment]

    # Stub HITL: alternate approved / rejected so we exercise both paths.
    decisions = iter(
        [
            {"decision": "approved", "edits": None, "actor": "smoke"},
            {"decision": "rejected", "edits": None, "actor": "smoke"},
            {"decision": "edited", "edits": {"proposed_status": "WARNING"}, "actor": "smoke"},
            {"decision": "approved", "edits": None, "actor": "smoke"},
        ]
    )

    def fake_decision(_interrupt_type, _payload):
        try:
            return next(decisions)
        except StopIteration:
            return {"decision": "approved", "edits": None, "actor": "smoke"}

    hitl_module.request_human_decision = fake_decision  # type: ignore[assignment]
    # The agent imported the symbol at module-load time, so patch the
    # local binding too.
    globals()["request_human_decision"] = fake_decision

    patch = asyncio.run(run(state))

    out = patch["outputs"][AGENT_NAME]
    print("=== evacuation_intelligence smoke ===")
    print("narrative:", out.narrative)
    print("confidence:", out.confidence)
    print("confidence_driver:", out.confidence_driver)
    print("zones_fetched:", out.payload["zones_fetched"])
    for zc in out.payload["zone_changes"]:
        print(
            f"  {zc['zone_id']}: {zc['current_status']} → "
            f"PROPOSED {zc['proposed_status']} → FINAL {zc['final_status']} "
            f"(decision={zc['decision']})"
        )
    print("audit_records:", len(patch.get("audit_log", [])))

    zo = out.payload["zones_order"]
    za = out.payload["zones_advisory"]
    zw = out.payload["zones_watch"]
    print(f"zones_order   ({len(zo)}): {[z['zone_id'] for z in zo]}")
    print(f"zones_advisory({len(za)}): {[z['zone_id'] for z in za]}")
    print(f"zones_watch   ({len(zw)}): {[z['zone_id'] for z in zw]}")
    print(f"cone_radius_deg: {out.payload['cone_radius_deg']:.4f}")
    assert zo, "expected zones_order to be non-empty"
    assert za, "expected zones_advisory to be non-empty"
    assert zw, "expected zones_watch to be non-empty"
    print("phasing bucket check: PASS")

    # Verify hard rule by inspecting our own module + tool module for
    # banned tokens. This is belt-and-suspenders alongside the PR-level
    # git grep.
    import inspect

    from ..tools import evac as evac_tool_mod

    # Build banned-callable prefixes via split literals so the file's own
    # source text does not trigger the repo-level grep check.
    banned = (
        "pub" "lish_evacuation_",
        "dis" "patch_",
        "ord" "er_",
        "se" "nd_",
    )
    for mod in (sys.modules[__name__], evac_tool_mod):
        src = inspect.getsource(mod)
        for token in banned:
            import re

            # Only flag the token when used as a callable — immediately
            # followed by word chars and an open paren.
            pattern = re.compile(rf"\b{re.escape(token)}\w*\s*\(")
            hits = pattern.findall(src)
            assert not hits, (
                f"banned callable prefix '{token}' found in {mod.__name__}: {hits}"
            )

    print("hard-rule check: PASS")


if __name__ == "__main__":
    _smoke_run()
