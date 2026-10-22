"use client";

import { useStore } from "@/lib/store";
import { Package, Truck, Users, Plane, Briefcase } from "lucide-react";
import { AgentActivityBanner } from "./AgentActivityBanner";
import { AgentCard, KeyFindings, MetricGrid } from "./AgentCard";
import { AgentGate, GateNotice } from "@/app/components/ui/AgentGate";
import { AgentStatusRow } from "@/app/components/ui/AgentStatusRow";
import { AsOf, AttentionStrip, type AttentionItem } from "@/app/components/ui/AttentionStrip";
import { Disclosure, SectionLabel } from "@/app/components/ui/Disclosure";
import { type Severity, SeverityBadge } from "@/app/components/ui/Severity";
import { StatTile } from "@/app/components/ui/StatTile";
import { useAgentAsOf } from "@/app/components/ui/useAgentAsOf";
import {
  firstInLines,
  flattenEnvelope,
  fmtClock,
  fmtInt,
  fmtNum,
  pendingResourceDraft,
  routingDataGap,
  urgencyLevel,
  type ResourceLine,
  type ResourcePayload,
} from "@/app/components/ui/triage";

type ScoreComponents = {
  incident?: number;
  water?: number;
  station?: number;
  paved?: number;
  elevation?: number;
  slope?: number;
  wind?: number;
};

type WindRelation = "upwind" | "crosswind" | "downwind" | "unknown";

type RoutingPayload = {
  stub?: boolean;
  reason?: string;
  osm_failures?: string[];
  graph_error?: string | null;
  candidates?: Array<{
    name?: string;
    loc?: [number, number];
    score?: number;
    dist_incident_km?: number;
    nearest_water_km?: number;
    tags?: Record<string, string>;
    score_components?: ScoreComponents;
    score_raw?: Record<string, number | null>;
  }>;
  counts?: Record<string, number>;
  fire_stations?: Array<{ name?: string; dist_km?: number }>;
  key_findings?: string[];
  wind?: {
    from_deg?: number | null;
    speed_mph?: number | null;
    source?: string | null;
  };
  egress_routes?: Array<{
    bearing?: string;
    bearing_deg?: number;
    length_km?: number;
    est_drive_minutes?: number;
    wind_relation?: WindRelation;
    destination?: {
      name?: string;
      rally_type?: string;
      source?: string;
      capacity?: number | null;
      score?: number;
    };
  }>;
  road_density_km_per_km2?: number;
  rally_points?: Array<{
    name?: string;
    rally_type?: string;
    source?: string;
    capacity?: number | null;
    score?: number;
    wind_relation?: WindRelation;
    score_raw?: { distance_km?: number | null };
  }>;
  rally_counts?: { osm?: number; hifld?: number; calfire?: number; total_raw?: number; after_dedup_ranked?: number };
  rally_source_failures?: string[];
  egress_strategy?: "rally_points" | "bearings_fallback";
};

const RESOURCE_AGENTS = ["resource_recommendation", "routing_staging"] as const;

const KIND_GROUPS: Array<{ kind: string; label: string; Icon: typeof Truck }> = [
  { kind: "apparatus", label: "Apparatus", Icon: Truck },
  { kind: "crew", label: "Crews", Icon: Users },
  { kind: "aircraft", label: "Aircraft", Icon: Plane },
  { kind: "overhead", label: "Overhead", Icon: Briefcase },
];

// Wind relation of an egress route or rally point, as colour + icon + word.
// Downwind means travelling the way the fire is being pushed.
const WIND_REL: Record<WindRelation, { level: Severity; word: string }> = {
  upwind: { level: "ok", word: "Upwind" },
  crosswind: { level: "warning", word: "Crosswind" },
  downwind: { level: "critical", word: "Downwind" },
  unknown: { level: "info", word: "Wind ?" },
};

// The stub agent appends the same "[cone=…; VAR=…; fuel=…]" basis to every
// line. Show that basis once above the table and keep each row short.
function shortRationale(r?: string): string {
  return (r ?? "").replace(/\s*\[cone=[^\]]*\]\.?\s*$/, "").trim();
}

export function ResourcesTab() {
  const routing = useStore((s) => s.agentOutputs.routing_staging);
  const routingStatus = useStore((s) => s.agentStatuses.routing_staging ?? "pending");
  const resource = useStore((s) => s.agentOutputs.resource_recommendation);
  const resourceStatus = useStore((s) => s.agentStatuses.resource_recommendation ?? "pending");
  const pending = useStore((s) => s.pendingInterrupts);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const requestRestart = useStore((s) => s.requestRestart);
  const done = useStore((s) => s.done);
  const streaming = useStore((s) => s.streaming);
  const asOf = useAgentAsOf(RESOURCE_AGENTS);
  const runFinished = done && !streaming;

  const rp = (routing?.payload ?? {}) as RoutingPayload;
  const rrp = (resource?.payload ?? {}) as ResourcePayload;
  const draft = pendingResourceDraft(pending);

  // Which lines to show: the approved/edited output, else the DRAFT that is
  // waiting in the approval queue, else a rejected draft for the record.
  let lines: ResourceLine[] = [];
  let orderState: "draft" | "approved" | "edited" | "rejected" | null = null;
  if (resource && rrp.status !== "rejected") {
    lines =
      rrp.recommendations ??
      (rrp.draft_resources ?? []).map((label) => ({ kind: label, type: label }));
    orderState = rrp.status === "edited" ? "edited" : "approved";
  } else if (draft) {
    lines = flattenEnvelope(draft.envelope);
    orderState = "draft";
  } else if (resource && rrp.status === "rejected") {
    lines = flattenEnvelope(rrp.draft);
    orderState = "rejected";
  }
  const urgency = draft?.urgency ?? rrp.urgency ?? rrp.recommendation?.urgency;
  const expiresAt = draft?.expiresAt ?? rrp.expires_at;
  const summary =
    draft?.envelope.rationale_summary ?? rrp.rationale_summary ?? rrp.recommendation?.rationale_summary;
  const unitCount = lines.reduce((n, l) => n + (l.quantity ?? 0), 0);
  const routingGap = routing ? routingDataGap(rp, routing.confidence) : null;
  const candidates = (rp.candidates ?? []).slice(0, 5);
  const egressRoutes = (rp.egress_routes ?? []).slice(0, 6);
  const rallyPoints = (rp.rally_points ?? []).slice(0, 8);
  const rallyCounts = rp.rally_counts ?? {};
  const goToQueue = () => setActiveTab("Operations");

  // ---------------- attention strip ----------------
  const attention: AttentionItem[] = [];
  if (orderState === "draft") {
    attention.push({
      id: "order",
      level: "warning",
      value: fmtInt(unitCount),
      unit: "units",
      title: "Resource order DRAFT is waiting for your approval",
      detail: [
        urgency ? `Urgency ${urgency.toUpperCase()}.` : null,
        fmtClock(expiresAt) ? `Draft expires ${fmtClock(expiresAt)}.` : null,
        "Nothing is ordered until you approve.",
      ]
        .filter(Boolean)
        .join(" "),
      action: { label: "Review in approval queue", onClick: goToQueue },
    });
  } else if (orderState === "approved" || orderState === "edited") {
    attention.push({
      id: "order",
      level: urgencyLevel(urgency),
      value: fmtInt(unitCount),
      unit: "units",
      title: `RECOMMENDED order, ${orderState} by IC`,
      detail: urgency ? `Urgency ${urgency.toUpperCase()}. Place the order through your dispatch center.` : undefined,
    });
  } else if (orderState === "rejected") {
    attention.push({
      id: "order",
      level: "info",
      title: "Resource DRAFT was rejected",
      detail: rrp.reason ? `Reason: ${rrp.reason}` : "No reason recorded.",
    });
  } else if (runFinished && resourceStatus !== "running") {
    attention.push({
      id: "order",
      level: "degraded",
      title: "No resource recommendation this run",
      detail:
        "The Resource agent did not report. Size the initial order from your run cards and the Mobilization Guide.",
      action: { label: "Re-brief", onClick: requestRestart },
    });
  }
  const firstIn = firstInLines(lines);
  if (firstIn.length > 0) {
    attention.push({
      id: "first-in",
      level: "info",
      value: fmtInt(firstIn.reduce((n, l) => n + (l.quantity ?? 0), 0)),
      unit: "units in first 2 h",
      title: "Fastest arrivals in the PROPOSED order",
      detail: firstIn.map((l) => `${l.quantity}× ${l.type ?? l.kind}`).join(", "),
    });
  }
  if (routingGap) {
    attention.push({
      id: "staging",
      level: "degraded",
      title: "Staging areas unavailable",
      detail: `${routingGap} Choose staging from your pre-plan maps; Re-brief when the source is back.`,
    });
  } else if (candidates[0]) {
    const c = candidates[0];
    attention.push({
      id: "staging",
      level: "info",
      value: fmtNum(c.dist_incident_km ?? null, 1),
      unit: "km from fire",
      title: `Best staging candidate: ${c.name ?? "unnamed"}`,
      summary: `Best staging: ${c.name ?? "unnamed"}, ${fmtNum(c.dist_incident_km ?? null, 1)} km from fire`,
      detail:
        c.nearest_water_km != null ? `Water ${fmtNum(c.nearest_water_km, 2)} km away.` : undefined,
    });
  }
  const downwind = egressRoutes.filter((r) => r.wind_relation === "downwind");
  if (downwind.length > 0) {
    attention.push({
      id: "egress",
      level: "warning",
      value: `${downwind.length}/${egressRoutes.length}`,
      unit: "routes",
      title: "Egress routes run downwind, toward the fire's path",
      detail: downwind.map((r) => r.destination?.name ?? `bearing ${r.bearing ?? "?"}`).join(", "),
    });
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-5">
        <header className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h2 className="text-xl font-semibold text-smoke-100">Resources — RECOMMEND</h2>
            <AsOf ts={asOf} />
          </div>
          <p className="text-[12px] text-smoke-300">
            Header verb is always <span className="font-semibold text-ember-200">RECOMMEND</span>.
            EmberSight never dispatches; every line below requires IC approval.
          </p>
          <AgentStatusRow agents={RESOURCE_AGENTS} gaps={{ routing_staging: routingGap }} />
        </header>

        <AttentionStrip items={attention} emptyText="Waiting for the Resource and Routing agents." />

        {/* ---------------- Order ---------------- */}
        <section className="space-y-3 rounded-md border border-smoke-700 bg-smoke-800/40 p-4">
          <SectionLabel right="Resource Recommendation agent">PROPOSED resource order</SectionLabel>
          {orderState ? (
            <>
              <div className="flex flex-wrap items-center gap-2 text-[13px] text-smoke-200">
                {orderState === "draft" && (
                  <>
                    <SeverityBadge level="warning" word="Draft · awaiting approval" size="md" />
                    <button
                      onClick={goToQueue}
                      className="rounded border border-smoke-600 bg-smoke-700 px-2.5 py-1 text-[12px] font-semibold text-smoke-100 hover:border-ember-500"
                    >
                      Open approval queue
                    </button>
                  </>
                )}
                {(orderState === "approved" || orderState === "edited") && (
                  <SeverityBadge level="ok" word={`RECOMMENDED · ${orderState}`} size="md" />
                )}
                {orderState === "rejected" && <SeverityBadge level="info" word="Rejected" size="md" />}
                {urgency && (
                  <SeverityBadge level={urgencyLevel(urgency)} word={`Urgency ${urgency}`} size="md" />
                )}
                {fmtClock(expiresAt) && (
                  <span className="text-[12px] text-smoke-300">
                    {orderState === "draft" ? "Draft" : "Recommendation"} valid until {fmtClock(expiresAt)}
                  </span>
                )}
              </div>
              <OrderTotals lines={lines} />
              {summary && (
                <p className="text-[12px] leading-relaxed text-smoke-300">
                  <span className="font-semibold text-smoke-200">Sized to: </span>
                  {summary}
                </p>
              )}
              {(rrp.missing_inputs?.length ?? 0) > 0 && (
                <GateNotice tone="degraded">
                  <SeverityBadge level="degraded" /> Sized without input from:{" "}
                  {rrp.missing_inputs!.join(", ")}. Treat quantities as a floor.
                </GateNotice>
              )}
              <OrderTable lines={lines} />
            </>
          ) : (
            <AgentGate
              agent="resource_recommendation"
              fallbackHint="Size the initial order from your run cards and the CAL FIRE Mobilization Guide."
            >
              <GateNotice>No line items in this recommendation.</GateNotice>
            </AgentGate>
          )}
        </section>

        {/* ---------------- Staging & egress ---------------- */}
        <section className="space-y-3 rounded-md border border-smoke-700 bg-smoke-800/40 p-4">
          <SectionLabel right="Routing & Staging agent · OpenStreetMap">Staging &amp; egress</SectionLabel>
          <AgentGate
            agent="routing_staging"
            fallbackHint="Choose staging from your pre-plan maps."
          >
            {routingGap && (
              <GateNotice tone="degraded">
                <span className="font-semibold">Staging data unavailable.</span> {routingGap} Choose
                staging and egress from your pre-plan maps or with ICS Planning; Re-brief once the
                map service is back. Agent confidence {Math.round((routing?.confidence ?? 0) * 100)}%.
              </GateNotice>
            )}

            {rp.wind && rp.wind.from_deg != null && (
              <p className="text-[12.5px] text-smoke-200">
                Wind from <span className="font-semibold">{Number(rp.wind.from_deg).toFixed(0)}°</span>
                {rp.wind.speed_mph != null ? (
                  <>
                    {" "}at <span className="font-semibold">{Number(rp.wind.speed_mph).toFixed(0)} mph</span>
                  </>
                ) : null}
                <span className="text-smoke-400">
                  {" "}· fire head pushed toward {Number(((rp.wind.from_deg ?? 0) + 180) % 360).toFixed(0)}°
                </span>
              </p>
            )}

            {candidates.length > 0 && <StagingCandidates candidates={candidates} />}

            {egressRoutes.length > 0 && (
              <div>
                <SectionLabel
                  right={
                    rp.egress_strategy === "bearings_fallback"
                      ? "bearing fallback (no rally points reached)"
                      : "to defined rally points · wind-ranked"
                  }
                >
                  Egress routes
                </SectionLabel>
                <ul className="space-y-1">
                  {egressRoutes.map((r, i) => {
                    const dest = r.destination;
                    const rel = WIND_REL[r.wind_relation ?? "unknown"];
                    return (
                      <li
                        key={i}
                        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded bg-smoke-900/60 px-2.5 py-1.5 text-[12.5px]"
                      >
                        <SeverityBadge level={rel.level} word={rel.word} />
                        <span className="min-w-0 flex-1 truncate font-medium text-smoke-100">
                          {dest?.name ?? `bearing ${r.bearing ?? "?"}`}
                          {dest?.rally_type && (
                            <span className="ml-1.5 text-[11px] uppercase tracking-wider text-smoke-400">
                              {dest.rally_type.replace(/_/g, " ")}
                            </span>
                          )}
                        </span>
                        <span className="shrink-0 tabular-nums text-smoke-200">
                          {r.length_km != null ? `${r.length_km.toFixed(1)} km` : ""}
                          {r.est_drive_minutes != null ? ` · ${Math.round(r.est_drive_minutes)} min drive` : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            {rallyPoints.length > 0 && (
              <Disclosure
                tone="plain"
                title="Rally points"
                meta={`osm ${rallyCounts.osm ?? 0} · hifld ${rallyCounts.hifld ?? 0}${
                  rallyCounts.calfire ? ` · cal fire ${rallyCounts.calfire}` : ""
                }`}
              >
                <ul className="space-y-1">
                  {rallyPoints.map((p, i) => {
                    const rel = WIND_REL[p.wind_relation ?? "unknown"];
                    return (
                      <li
                        key={i}
                        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded bg-smoke-900/60 px-2.5 py-1.5 text-[12.5px]"
                      >
                        <SeverityBadge level={rel.level} word={rel.word} />
                        <span className="min-w-0 flex-1 truncate font-medium text-smoke-100">
                          {p.name ?? "rally point"}
                          <span className="ml-1.5 text-[11px] uppercase tracking-wider text-smoke-400">
                            {(p.rally_type ?? "?").replace(/_/g, " ")}
                          </span>
                        </span>
                        <span className="shrink-0 tabular-nums text-smoke-200">
                          {p.score_raw?.distance_km != null
                            ? `${Number(p.score_raw.distance_km).toFixed(1)} km`
                            : ""}
                          {p.capacity != null && Number(p.capacity) > 0
                            ? ` · ~${Number(p.capacity).toLocaleString()} capacity`
                            : ""}
                          {p.score != null ? ` · score ${Number(p.score).toFixed(2)}` : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                {rp.rally_source_failures && rp.rally_source_failures.length > 0 && (
                  <p className="mt-1 text-[11px] text-smoke-400">
                    Source failures: {rp.rally_source_failures.join(", ")}
                  </p>
                )}
              </Disclosure>
            )}

            {(rp.fire_stations?.length ?? 0) > 0 && (
              <Disclosure tone="plain" title="Nearest fire stations" meta={`${rp.fire_stations!.length} closest`}>
                <ul className="space-y-1 text-[12.5px]">
                  {rp.fire_stations!.map((s, i) => (
                    <li key={i} className="flex justify-between rounded bg-smoke-900/60 px-2.5 py-1">
                      <span className="text-smoke-100">{s.name ?? "fire station"}</span>
                      <span className="tabular-nums text-smoke-300">{fmtNum(s.dist_km ?? null, 1)} km</span>
                    </li>
                  ))}
                </ul>
              </Disclosure>
            )}

            {rp.counts && Object.keys(rp.counts).length > 0 && (
              <Disclosure
                tone="plain"
                title="Map data coverage"
                meta={
                  rp.road_density_km_per_km2 != null
                    ? `road density ${rp.road_density_km_per_km2} km/km²`
                    : undefined
                }
              >
                <MetricGrid
                  items={Object.entries(rp.counts).map(([k, v]) => ({
                    label: k.replace(/_/g, " "),
                    value: v,
                  }))}
                />
              </Disclosure>
            )}
          </AgentGate>
        </section>

        {/* ---------------- Full reports ---------------- */}
        <Disclosure title="Full agent reports" meta="narratives · key findings · confidence drivers · citations">
          <AgentCard
            title="Resource Recommendation"
            subtitle="Engines · crews · dozers · air"
            status={resourceStatus}
            output={resource}
          >
            {rrp.key_findings && <KeyFindings items={rrp.key_findings} />}
          </AgentCard>
          <AgentCard
            title="Routing & Staging"
            subtitle="OSM road/water joins · candidate staging areas"
            status={routingStatus}
            output={routing}
          >
            {rp.key_findings && <KeyFindings items={rp.key_findings} />}
          </AgentCard>
        </Disclosure>

        <Disclosure title="Agent activity log" meta="live event ticker">
          <AgentActivityBanner
            title="Resource Posture"
            subtitle="Resources · Routing & Staging"
            agents={["resource_recommendation", "routing_staging"]}
            icon={<Package className="h-5 w-5" />}
          />
        </Disclosure>
      </div>
    </div>
  );
}

function OrderTotals({ lines }: { lines: ResourceLine[] }) {
  const total = lines.reduce((n, l) => n + (l.quantity ?? 0), 0);
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
      <StatTile label="Total" value={fmtInt(total)} unit="units" />
      {KIND_GROUPS.map(({ kind, label, Icon }) => {
        const n = lines
          .filter((l) => (l.kind ?? "").toLowerCase() === kind)
          .reduce((s, l) => s + (l.quantity ?? 0), 0);
        return (
          <StatTile
            key={kind}
            label={label}
            icon={<Icon className="h-3.5 w-3.5" aria-hidden />}
            value={fmtInt(n)}
            unit="units"
          />
        );
      })}
    </div>
  );
}

function OrderTable({ lines }: { lines: ResourceLine[] }) {
  const known = new Set(KIND_GROUPS.map((g) => g.kind));
  const groups = [
    ...KIND_GROUPS.map((g) => ({
      label: g.label,
      rows: lines.filter((l) => (l.kind ?? "").toLowerCase() === g.kind),
    })),
    { label: "Other", rows: lines.filter((l) => !known.has((l.kind ?? "").toLowerCase())) },
  ].filter((g) => g.rows.length > 0);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-[13px]">
        <thead className="text-[11px] uppercase tracking-wider text-smoke-400">
          <tr>
            <th className="w-14 py-1 pr-3 text-right font-semibold">Qty</th>
            <th className="py-1 pr-3 font-semibold">Resource</th>
            <th className="py-1 pr-3 font-semibold">Arrival window</th>
            <th className="py-1 font-semibold">Drive to staging</th>
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody key={g.label} className="text-smoke-100">
            <tr>
              <th
                colSpan={4}
                className="pb-1 pt-3 text-[11px] font-bold uppercase tracking-widest text-smoke-300"
              >
                {g.label}
              </th>
            </tr>
            {g.rows.map((l, i) => (
              <tr key={`${l.type}-${i}`} className="border-t border-smoke-700/70 align-top">
                <td className="py-1.5 pr-3 text-right text-[16px] font-bold tabular-nums">
                  {l.quantity ?? "—"}
                  <span className="text-[12px] font-semibold text-smoke-400">×</span>
                </td>
                <td className="py-1.5 pr-3">
                  <div className="font-semibold">{l.type ?? l.kind ?? "resource"}</div>
                  {l.rationale && (
                    <div className="text-[12px] leading-snug text-smoke-300">
                      {shortRationale(l.rationale)}
                    </div>
                  )}
                </td>
                <td className="py-1.5 pr-3 tabular-nums">
                  {l.arrival_window ? `${l.arrival_window.replace("-", "–")}` : "—"}
                </td>
                <td className="py-1.5 tabular-nums">
                  {l.distance_to_staging_min != null ? `${l.distance_to_staging_min} min` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

const COMPONENT_LABELS: Array<[keyof ScoreComponents, string]> = [
  ["incident", "Fire distance"],
  ["water", "Water"],
  ["station", "Station"],
  ["paved", "Paved"],
  ["elevation", "Elevation"],
  ["slope", "Slope"],
  ["wind", "Wind"],
];

function StagingCandidates({
  candidates,
}: {
  candidates: NonNullable<RoutingPayload["candidates"]>;
}) {
  return (
    <div>
      <SectionLabel right="score 0–1, higher is better">Top staging candidates</SectionLabel>
      <ol className="space-y-1.5">
        {candidates.map((c, i) => (
          <li
            key={i}
            className={`rounded-md bg-smoke-900/60 px-3 py-2 ${i === 0 ? "ring-1 ring-smoke-600" : ""}`}
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-[13.5px] font-semibold text-smoke-100">
                <span className="mr-1.5 text-smoke-400">#{i + 1}</span>
                {c.name ?? `candidate ${i + 1}`}
              </span>
              <span className="text-[12px] tabular-nums text-smoke-300">
                score {typeof c.score === "number" ? c.score.toFixed(2) : "—"}
              </span>
            </div>
            <div className="mt-0.5 text-[12px] text-smoke-300">
              {[
                c.dist_incident_km != null ? `${c.dist_incident_km.toFixed(1)} km from fire` : null,
                c.nearest_water_km != null ? `water ${c.nearest_water_km.toFixed(2)} km` : null,
                c.score_raw?.slope_pct != null ? `slope ${Number(c.score_raw.slope_pct).toFixed(1)}%` : null,
                c.tags?.surface ?? null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </div>
            {c.score_components && (
              <Disclosure tone="plain" title={<span className="text-[12px]">Score breakdown</span>}>
                <ScoreBreakdown components={c.score_components} />
              </Disclosure>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ScoreBreakdown({ components }: { components: ScoreComponents }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
      {COMPONENT_LABELS.map(([k, label]) => {
        const v = components[k];
        const pct = typeof v === "number" ? Math.max(0, Math.min(100, v * 100)) : 0;
        const level: Severity | null =
          typeof v !== "number" ? null : v >= 0.7 ? "ok" : v >= 0.4 ? "warning" : "critical";
        return (
          <div key={k} className="text-[11px]">
            <div className="flex justify-between text-smoke-300">
              <span>{label}</span>
              <span className="tabular-nums text-smoke-100">
                {typeof v === "number" ? v.toFixed(2) : "—"}
              </span>
            </div>
            <div className="mt-0.5 h-1.5 overflow-hidden rounded-sm bg-smoke-700">
              <div
                className={
                  level === "ok"
                    ? "h-full bg-sev-ok-edge"
                    : level === "warning"
                      ? "h-full bg-sev-warning-edge"
                      : "h-full bg-sev-critical-edge"
                }
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
