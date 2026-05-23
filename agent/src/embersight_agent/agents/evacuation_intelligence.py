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


def _llm_proposal(
    zone: dict,
    overlaps: dict,
    population: int,
    egress: dict,
) -> tuple[str, str] | None:
    """Ask Claude Haiku 4.5 for a proposed status + rationale.

    Returns `(proposed_status, rationale)` or `None` if the LLM call
    fails / the model output cannot be parsed. The caller falls back to
    the deterministic proposal.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    try:
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
    except ImportError:
        return None

    try:
        from pathlib import Path  # noqa: PLC0415

        prompt_path = (
            Path(__file__).resolve().parent.parent
            / "prompts"
            / "evacuation_intelligence.md"
        )
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError:
        system_prompt = (
            "You PROPOSE Cal OES evacuation zone status changes. Verbs are "
            "PROPOSE / RECOMMEND only. Never issue / publish / order."
        )

    user_msg = (
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

    try:
        chat = ChatAnthropic(model=LLM_MODEL, max_tokens=200, temperature=0.0)
        resp = chat.invoke(
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
# Main entry point
# --------------------------------------------------------------------------- #


async def run(state: AgentState) -> dict:  # noqa: C901 — orchestration is naturally branchy
    incident = state.incident
    fetch_errors = 0

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

    # ---- Cal OES zone fetch ------------------------------------------- #
    bbox = _bbox_around_incident(incident)
    zones: list[dict] = []
    if bbox is not None:
        try:
            zones = await evac_tools.get_calevacs_zones(bbox)
        except Exception as exc:  # noqa: BLE001
            log.warning("get_calevacs_zones raised: %s", exc)
            fetch_errors += 1
            zones = []

    # ---- Per-zone analysis + interrupts ------------------------------- #
    zone_changes: list[dict] = []
    audit_records = []
    key_findings: list[str] = []
    counts = {"proposed": 0, "approved": 0, "edited": 0, "rejected": 0}

    active_evac_geoms = [
        _coerce_geom(z["polygon_wkt"])
        for z in zones
        if z["current_status"] == "ORDER"
    ]
    active_evac_geoms = [g for g in active_evac_geoms if g is not None]

    for zone in zones:
        zone_geom = _coerce_geom(zone["polygon_wkt"])
        overlaps = {
            "6h": _overlap_fraction(zone_geom, cone_6h),
            "12h": _overlap_fraction(zone_geom, cone_12h),
            "24h": _overlap_fraction(zone_geom, cone_24h),
        }

        # Adjacency nudge: if a NORMAL zone borders an already-active evac
        # zone (within ~250 m), bias it toward WARNING so the LLM/rule
        # is more aggressive about proposing an upgrade.
        adjacent_to_order = False
        if zone_geom is not None and active_evac_geoms:
            try:
                buffered = zone_geom.buffer(0.0025)  # ~250 m at CA latitudes
                adjacent_to_order = any(
                    buffered.intersects(g) for g in active_evac_geoms
                )
            except Exception:  # noqa: BLE001
                adjacent_to_order = False

        population = evac_tools.estimate_population(zone["polygon_wkt"])
        egress = evac_tools.compute_evacuation_routes_clear(
            zone["polygon_wkt"],
            road_graph,
            spread_cone=cones.get("6h"),
        )

        # Bias overlaps slightly upward if zone is adjacent to an ORDER
        # zone — the deterministic rule below will then favor WARNING.
        effective_overlaps = dict(overlaps)
        if adjacent_to_order and effective_overlaps["12h"] < 0.10:
            effective_overlaps["12h"] = 0.10

        llm_out = _llm_proposal(zone, effective_overlaps, population, egress)
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
        envelope = {
            "type": "evac_zone_change",
            "zone_id": zone["zone_id"],
            "name": zone["name"],
            "current_status": zone["current_status"],
            "proposed_status": proposed_status,
            "rationale": rationale,
            "rationale_source": rationale_source,
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
        f"{counts['proposed']} zone changes PROPOSED, "
        f"{counts['approved']} approved, "
        f"{counts['edited']} edited, "
        f"{counts['rejected']} rejected"
    )

    output = AgentOutput(
        agent=AGENT_NAME,
        narrative=summary,
        payload={
            "zone_changes": zone_changes,
            "zones_fetched": len(zones),
            "counts": counts,
            "bbox": bbox,
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
    routing_out = AgentOutput(
        agent="routing_staging",
        narrative="mock routing (no graph)",
        payload={"road_graph": None},
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
