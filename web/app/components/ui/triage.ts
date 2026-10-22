/**
 * Triage helpers shared by the Briefing, Threats and Resources tabs.
 *
 * Agents return loosely-typed payloads (Record<string, unknown>). These
 * types mirror what the Python agents actually emit (see
 * agent/src/embersight_agent/agents/*.py) and the functions below turn
 * them into a short list of "what needs attention now" items.
 *
 * Rule followed everywhere: a failed data source is reported as a DATA
 * GAP, never as a zero. "0 hospitals" from a source that errored would
 * read as an all-clear to an IC under pressure.
 */
import type { AgentOutput, AgentStatus, PendingInterrupt } from "@/lib/store";
import type { AttentionItem } from "./AttentionStrip";
import type { Severity } from "./Severity";

// --------------------------------------------------------------------------
// Payload shapes (subset of what each agent emits)
// --------------------------------------------------------------------------

export type ValuesRollup = {
  structures_total?: number;
  total_footprint_sqm?: number;
  structures_by_occupancy?: Record<string, number>;
  residential_count?: number;
  commercial_count?: number;
  public_count?: number;
  industrial_count?: number;
  hospitals_count?: number;
  hospitals_total_beds?: number;
  named_hospitals?: Array<{ name?: string; beds?: number }>;
  schools_count?: number;
  schools_total_enrollment?: number;
  named_schools?: Array<{ name?: string; enrollment?: number }>;
  transmission_segments?: number;
  transmission_max_kv?: number;
  critical_facilities_total?: number;
};

export type ValuesPayload = {
  rollup?: ValuesRollup;
  key_findings?: string[];
  cone_source?: string;
  tallies?: Record<string, unknown>;
};

export type ConeImpact = {
  population_estimate?: number;
  structures_total?: number;
  residential_count?: number;
  hospitals_count?: number;
  schools_count?: number;
  critical_facilities_total?: number;
  error?: string;
};

export type SpreadPayload = {
  key_findings?: string[];
  head_ros_chains_per_hr?: number | null;
  head_ros_fpm_mean?: number | null;
  head_ros_fpm_std?: number | null;
  flame_length_ft?: number | null;
  burn_area_24h_km2_p25?: number | null;
  trigger_breaches?: Array<{
    trigger_id?: string;
    hours_until_breach?: number;
    prob_at_breach?: number;
  }>;
  high_risk_zones?: Array<{
    id?: string;
    sector?: string;
    bearing_deg?: number;
    distance_km?: number;
    horizon_h?: number;
    probability_min?: number;
    area_km2?: number;
  }>;
  cone_impact?: ConeImpact | null;
  n_mc_samples?: number;
  fuel_model?: string;
  wind_speed_mph?: number;
  wind_dir_deg?: number;
};

export type TerrainPayload = {
  fuel_model?: string | { error?: string };
  slope_pct?: number;
  aspect_deg?: number;
  aoi_radius_km?: number;
  fuel_detail?: {
    pixels?: number;
    dominant_classes?: Array<{ code?: string; fraction?: number; name?: string; pct?: number }>;
    purity?: number;
    error?: string;
  };
  terrain?: {
    slope_deg?: Record<string, number>;
    elevation_m?: Record<string, number>;
    aspect_distribution?: Record<string, number>;
    error?: string;
  };
  canopy?: { mean_pct?: number; p10_pct?: number; p90_pct?: number; pixels?: number; error?: string };
  key_findings?: string[];
  fetch_error?: string | null;
};

export type ResourceLine = {
  kind?: string;
  type?: string;
  quantity?: number;
  rationale?: string;
  distance_to_staging_min?: number | null;
  arrival_window?: string | null;
};

export type ResourceEnvelope = {
  urgency?: string;
  apparatus?: ResourceLine[];
  crews?: ResourceLine[];
  aircraft?: ResourceLine[];
  overhead?: ResourceLine[];
  rationale_summary?: string;
  expires_at?: string | null;
};

export type ResourcePayload = {
  status?: string;
  reason?: string;
  draft_resources?: string[];
  recommendations?: ResourceLine[];
  recommendation?: ResourceEnvelope;
  draft?: ResourceEnvelope;
  urgency?: string;
  expires_at?: string | null;
  rationale_summary?: string;
  key_findings?: string[];
  missing_inputs?: string[];
  unit_count?: number;
};

// --------------------------------------------------------------------------
// Formatting
// --------------------------------------------------------------------------

export function fmtInt(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

export function fmtNum(n: number | null | undefined, digits = 1): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

/** km² → acres; ICs size fires in acres. */
export function km2ToAcres(km2: number): number {
  return km2 * 247.105;
}

/** Cut text at a word boundary and add an ellipsis, never mid-word. */
export function truncateWords(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut).replace(/[\s,;:—-]+$/, "")}…`;
}

export function stripAgentPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
}

export function fmtClock(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// --------------------------------------------------------------------------
// Data-quality checks
// --------------------------------------------------------------------------

/** Human names for the six values-at-risk sources (payload.tallies keys). */
export const VALUES_SOURCES: Record<string, string> = {
  ms_buildings: "Building footprints",
  usa_structures: "FEMA structure occupancy",
  hospitals: "HIFLD hospitals",
  schools: "HIFLD schools",
  transmission: "HIFLD transmission lines",
  critical_facilities: "HIFLD critical facilities",
};

function sourceErrored(v: unknown): boolean {
  if (!v) return false;
  if (Array.isArray(v)) {
    const first = v[0] as Record<string, unknown> | undefined;
    return !!(first && typeof first === "object" && first.error);
  }
  if (typeof v === "object") return !!(v as Record<string, unknown>).error;
  return false;
}

/** Keys of values-at-risk sources that errored on this run. */
export function failedValuesSources(vp: ValuesPayload): string[] {
  const t = vp.tallies ?? {};
  return Object.keys(VALUES_SOURCES).filter((k) => sourceErrored(t[k]));
}

/** LANDFIRE returns -9999 (nodata) as a "class" when the raster is empty. */
export function topFuelClass(tp: TerrainPayload): { code: string; fraction: number | null; nodata: boolean } | null {
  const classes = tp.fuel_detail?.dominant_classes ?? [];
  const top = classes[0];
  if (!top) return null;
  const code = String(top.code ?? top.name ?? "?");
  const fraction =
    typeof top.fraction === "number" ? top.fraction : typeof top.pct === "number" ? top.pct : null;
  return { code, fraction, nodata: /-9999|unknown/i.test(code) };
}

export function terrainDataGap(tp: TerrainPayload): string | null {
  if (tp.fetch_error) return `LANDFIRE fetch failed: ${tp.fetch_error}`;
  const top = topFuelClass(tp);
  if (top?.nodata) {
    return `Dominant fuel class is "no data" (${top.code}) for ${fmtNum((top.fraction ?? 0) * 100, 0)}% of the area; verify fuel model in the field.`;
  }
  return null;
}

export type RoutingLike = {
  stub?: boolean;
  reason?: string;
  osm_failures?: string[];
  graph_error?: string | null;
  candidates?: unknown[];
};

export function routingDataGap(rp: RoutingLike, conf?: number): string | null {
  if (rp.stub) return rp.reason ?? "OpenStreetMap road data could not be fetched.";
  if ((rp.candidates?.length ?? 0) === 0 && (rp.osm_failures?.length ?? 0) > 0) {
    return `OpenStreetMap (Overpass) failed: ${rp.osm_failures!.join(", ")}`;
  }
  if (conf != null && conf < 0.2) return "Routing data badly degraded.";
  return null;
}

// --------------------------------------------------------------------------
// Fire-behaviour interpretation (NWCG "hauling chart" flame-length bands)
// --------------------------------------------------------------------------

export function flameLengthBand(ft: number): { level: Severity; meaning: string } {
  if (ft > 11) return { level: "critical", meaning: "Over 11 ft: beyond direct attack at the head; indirect tactics only." };
  if (ft >= 8) return { level: "critical", meaning: "8–11 ft: dozers, engines and retardant likely ineffective at the head." };
  if (ft >= 4) return { level: "warning", meaning: "4–8 ft: too intense for hand crews at the head; engines, dozers, air support." };
  return { level: "info", meaning: "Under 4 ft: hand crews can work the head directly." };
}

// --------------------------------------------------------------------------
// Run state
// --------------------------------------------------------------------------

/** An agent that never reported even though the run is over. */
export function agentMissing(status: AgentStatus | undefined, runFinished: boolean): boolean {
  return runFinished && (status === undefined || status === "pending");
}

// --------------------------------------------------------------------------
// Threats: attention items
// --------------------------------------------------------------------------

export function threatAttention(args: {
  values?: AgentOutput;
  spread?: AgentOutput;
  terrain?: AgentOutput;
}): AttentionItem[] {
  const items: AttentionItem[] = [];
  const vp = (args.values?.payload ?? {}) as ValuesPayload;
  const sp = (args.spread?.payload ?? {}) as SpreadPayload;
  const tp = (args.terrain?.payload ?? {}) as TerrainPayload;
  const r = vp.rollup ?? {};
  const failed = failedValuesSources(vp);

  // 1. Trigger points already modelled to be breached: the most time-bound fact.
  const breaches = [...(sp.trigger_breaches ?? [])].sort(
    (a, b) => (a.hours_until_breach ?? 99) - (b.hours_until_breach ?? 99),
  );
  if (breaches.length > 0) {
    const b = breaches[0];
    items.push({
      id: "trigger",
      level: "critical",
      value: fmtNum(b.hours_until_breach ?? null, 0),
      unit: "h to breach",
      title: `Trigger point ${b.trigger_id ?? "?"} projected to be reached`,
      detail: `${b.prob_at_breach != null ? `${Math.round(b.prob_at_breach * 100)}% probability. ` : ""}${
        breaches.length > 1 ? `${breaches.length} trigger points breached in total.` : ""
      } RECOMMEND trigger-point review.`,
    });
  }

  // 2. Structures inside the 24 h projected cone.
  if (args.values) {
    const structuresUnknown = failed.includes("ms_buildings");
    const people = sp.cone_impact && !sp.cone_impact.error ? sp.cone_impact.population_estimate : undefined;
    if (structuresUnknown) {
      items.push({
        id: "structures",
        level: "degraded",
        title: "Structure count unavailable",
        detail: "Building-footprint source failed. Do not read this as zero; re-brief or check the county parcel layer.",
      });
    } else {
      const total = r.structures_total ?? 0;
      const homes = r.residential_count ?? 0;
      const level: Severity =
        (r.hospitals_count ?? 0) > 0 || (r.schools_count ?? 0) > 0 || homes >= 250
          ? "critical"
          : total > 0
            ? "warning"
            : "ok";
      items.push({
        id: "structures",
        level,
        value: fmtInt(total),
        unit: "structures",
        title: "inside the 24 h projected spread cone",
        detail: [
          failed.includes("usa_structures") ? "homes: unknown (occupancy source failed)" : `${fmtInt(homes)} homes`,
          people != null ? `~${fmtInt(people)} people (est.)` : null,
        ]
          .filter(Boolean)
          .join(" · "),
      });
    }
  }

  // 3. Highest-consequence named asset.
  const hosp = (r.named_hospitals ?? [])[0];
  const school = (r.named_schools ?? [])[0];
  if (hosp?.name) {
    items.push({
      id: "asset",
      level: "critical",
      title: `Hospital in cone: ${hosp.name}`,
      value: fmtInt(hosp.beds ?? 0),
      unit: "beds",
      detail: `${fmtInt(r.hospitals_count)} hospital(s), ${fmtInt(r.hospitals_total_beds)} beds total. RECOMMEND verifying hospital evacuation plans.`,
    });
  } else if (school?.name) {
    items.push({
      id: "asset",
      level: "warning",
      title: `School in cone: ${school.name}`,
      value: fmtInt(school.enrollment ?? 0),
      unit: "students",
      detail: `${fmtInt(r.schools_count)} school(s), ${fmtInt(r.schools_total_enrollment)} enrolled in total.`,
    });
  } else if ((r.transmission_max_kv ?? 0) > 0) {
    items.push({
      id: "asset",
      level: "warning",
      title: "Transmission lines in cone",
      value: fmtInt(r.transmission_max_kv),
      unit: "kV max",
      detail: `${fmtInt(r.transmission_segments)} segment(s). RECOMMEND coordinating de-energization with the utility.`,
    });
  }

  // 4. Fire behaviour at the head.
  const fl = sp.flame_length_ft;
  if (typeof fl === "number" && fl > 0) {
    const band = flameLengthBand(fl);
    const ros = sp.head_ros_chains_per_hr;
    items.push({
      id: "flame",
      level: band.level,
      value: fmtNum(fl, 0),
      unit: "ft flame length",
      title: band.meaning,
      detail:
        typeof ros === "number"
          ? `Head spreading ${fmtNum(ros, 1)} chains/h (${fmtNum((ros * 66) / 5280, 1)} mph).`
          : undefined,
    });
  }

  // 5. Remaining data gaps (one item, so gaps never crowd out real threats).
  const gaps: string[] = [];
  const otherFailed = failed.filter((k) => k !== "ms_buildings");
  if (otherFailed.length) gaps.push(`${otherFailed.map((k) => VALUES_SOURCES[k]).join(", ")} failed`);
  const tGap = args.terrain ? terrainDataGap(tp) : null;
  if (tGap) gaps.push("fuel model unknown");
  if (sp.cone_impact?.error) gaps.push("spread-cone exposure query failed");
  if (gaps.length) {
    items.push({
      id: "gaps",
      level: "degraded",
      title: "Some threat data is missing",
      detail: `${gaps.join("; ")}. Counts below may be low.`,
    });
  }
  return items;
}

// --------------------------------------------------------------------------
// Resources
// --------------------------------------------------------------------------

export function flattenEnvelope(env: ResourceEnvelope | undefined): ResourceLine[] {
  if (!env) return [];
  return [
    ...(env.apparatus ?? []),
    ...(env.crews ?? []),
    ...(env.aircraft ?? []),
    ...(env.overhead ?? []),
  ].filter((l) => (l.quantity ?? 0) > 0);
}

export function urgencyLevel(u: string | undefined): Severity {
  const v = (u ?? "").toLowerCase();
  if (v === "high") return "critical";
  if (v === "med" || v === "medium") return "warning";
  return "info";
}

/** The pending resource draft, if the agent is paused waiting for the IC. */
export function pendingResourceDraft(pending: PendingInterrupt[]): {
  item: PendingInterrupt;
  envelope: ResourceEnvelope;
  urgency?: string;
  expiresAt?: string | null;
} | null {
  const item = pending.find((p) => p.interrupt.type === "resource_recommendation");
  if (!item) return null;
  const payload = (item.interrupt.payload ?? {}) as Record<string, unknown>;
  const envelope = (payload.recommendation ?? {}) as ResourceEnvelope;
  return {
    item,
    envelope,
    urgency: (payload.urgency as string) ?? envelope.urgency,
    expiresAt: (payload.expires_at as string) ?? envelope.expires_at,
  };
}

/** Lines that should arrive in the first two hours ("0-1h", "0-2h"). */
export function firstInLines(lines: ResourceLine[]): ResourceLine[] {
  return lines.filter((l) => /^0\s*[-–]/.test(l.arrival_window ?? ""));
}

// --------------------------------------------------------------------------
// Pending approvals → which agent is blocked
// --------------------------------------------------------------------------

export const INTERRUPT_AGENT: Record<string, string> = {
  resource_recommendation: "resource_recommendation",
  iap_approval: "master_ic",
  evac_zone_change: "evacuation_intelligence",
  trigger_point_violation: "spread_simulation",
};

export const INTERRUPT_LABEL: Record<string, string> = {
  resource_recommendation: "Resource order DRAFT",
  iap_approval: "IAP DRAFT",
  evac_zone_change: "Evacuation zone change PROPOSED",
  trigger_point_violation: "Trigger-point breach review",
};
