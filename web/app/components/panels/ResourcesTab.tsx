"use client";

import { useStore } from "@/lib/store";
import { Package } from "lucide-react";
import { AgentActivityBanner } from "./AgentActivityBanner";
import { AgentCard, KeyFindings, MetricGrid } from "./AgentCard";

type ScoreComponents = {
  incident?: number;
  water?: number;
  station?: number;
  paved?: number;
  elevation?: number;
  slope?: number;
  wind?: number;
};

type RoutingPayload = {
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
    wind_relation?: "upwind" | "crosswind" | "downwind" | "unknown";
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
    wind_relation?: "upwind" | "crosswind" | "downwind" | "unknown";
    score_raw?: { distance_km?: number | null };
  }>;
  rally_counts?: { osm?: number; hifld?: number; calfire?: number; total_raw?: number; after_dedup_ranked?: number };
  rally_source_failures?: string[];
  egress_strategy?: "rally_points" | "bearings_fallback";
};

type ResourcePayload = {
  draft_resources?: string[];
  recommendations?: Array<{
    kind?: string;
    quantity?: number;
    rationale?: string;
  }>;
  key_findings?: string[];
};

export function ResourcesTab() {
  const routing = useStore((s) => s.agentOutputs.routing_staging);
  const routingStatus = useStore(
    (s) => s.agentStatuses.routing_staging ?? "pending",
  );
  const resource = useStore((s) => s.agentOutputs.resource_recommendation);
  const resourceStatus = useStore(
    (s) => s.agentStatuses.resource_recommendation ?? "pending",
  );

  const rp = (routing?.payload ?? {}) as RoutingPayload;
  const rrp = (resource?.payload ?? {}) as ResourcePayload;

  const candidates = (rp.candidates ?? []).slice(0, 5);
  const counts = rp.counts ?? {};
  const egressRoutes = (rp.egress_routes ?? []).slice(0, 6);
  const rallyPoints = (rp.rally_points ?? []).slice(0, 8);
  const rallyCounts = rp.rally_counts ?? {};

  const routingMetrics: { label: string; value: string | number }[] = [];
  for (const k of Object.keys(counts)) {
    routingMetrics.push({ label: k, value: counts[k] });
  }

  const drafted = rrp.recommendations ?? deriveFromList(rrp.draft_resources);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">
            Resources — RECOMMEND
          </h2>
          <p className="text-xs text-smoke-400">
            Header verb is always{" "}
            <span className="text-ember-200">RECOMMEND</span>. EmberSight never
            dispatches — every line below requires IC approval.
          </p>
        </header>

        <AgentActivityBanner
          title="Resource Posture"
          subtitle="Resources · Routing & Staging"
          agents={["resource_recommendation", "routing_staging"]}
          icon={<Package className="h-5 w-5" />}
        />

        <AgentCard
          title="Resource Recommendation"
          subtitle="Engines · crews · dozers · air"
          status={resourceStatus}
          output={resource}
        >
          {drafted.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Proposed posture
              </div>
              <ul className="space-y-1">
                {drafted.map((d, i) => (
                  <li
                    key={i}
                    className="flex items-baseline justify-between rounded bg-smoke-900/60 px-2.5 py-1.5 text-[11px]"
                  >
                    <div>
                      <span className="text-ember-200">
                        {d.quantity ? `${d.quantity}× ` : ""}
                        {d.kind ?? "resource"}
                      </span>
                      {d.rationale && (
                        <span className="ml-1 text-smoke-400">
                          — {d.rationale}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rrp.key_findings && <KeyFindings items={rrp.key_findings} />}
        </AgentCard>

        <AgentCard
          title="Routing & Staging"
          subtitle="OSM road/water joins · candidate staging areas"
          status={routingStatus}
          output={routing}
        >
          <MetricGrid items={routingMetrics} />
          {rp.wind && rp.wind.from_deg != null && (
            <div className="text-[10px] text-smoke-400">
              Wind {Number(rp.wind.from_deg).toFixed(0)}°
              {rp.wind.speed_mph != null
                ? ` @ ${Number(rp.wind.speed_mph).toFixed(0)} mph`
                : ""}{" "}
              <span className="text-smoke-500">
                (FROM · fire-head heads{" "}
                {Number(((rp.wind.from_deg ?? 0) + 180) % 360).toFixed(0)}°)
              </span>
            </div>
          )}
          {egressRoutes.length > 0 && (
            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <div className="text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                  Egress routes (wind-ranked)
                </div>
                <div className="text-[9px] text-smoke-500">
                  {rp.egress_strategy === "bearings_fallback"
                    ? "bearing fallback (no rally points reached)"
                    : "→ defined rally points"}
                </div>
              </div>
              <ul className="space-y-0.5">
                {egressRoutes.map((r, i) => {
                  const dest = r.destination;
                  const label = dest?.name ?? `bearing ${r.bearing ?? "?"}`;
                  return (
                    <li
                      key={i}
                      className="flex items-baseline justify-between gap-2 rounded bg-smoke-900/60 px-2.5 py-1 text-[11px]"
                    >
                      <span className="flex min-w-0 items-baseline gap-1.5">
                        <span
                          className="inline-block h-2 w-2 shrink-0 rounded-sm"
                          style={{
                            backgroundColor: windRelationColor(r.wind_relation),
                          }}
                        />
                        <span className="truncate font-medium text-ember-200">
                          {label}
                        </span>
                        {dest?.rally_type && (
                          <span className="shrink-0 text-[9px] uppercase tracking-wider text-smoke-500">
                            {dest.rally_type.replace(/_/g, " ")}
                          </span>
                        )}
                      </span>
                      <span className="shrink-0 text-smoke-400">
                        {r.length_km != null
                          ? `${r.length_km.toFixed(1)} km`
                          : ""}
                        {r.est_drive_minutes != null
                          ? ` · ${Math.round(r.est_drive_minutes)} min`
                          : ""}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {rallyPoints.length > 0 && (
            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <div className="text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                  Rally points
                </div>
                <div className="text-[9px] text-smoke-500">
                  osm {rallyCounts.osm ?? 0} · hifld {rallyCounts.hifld ?? 0}
                  {rallyCounts.calfire
                    ? ` · cal fire ${rallyCounts.calfire}`
                    : ""}
                </div>
              </div>
              <ul className="space-y-0.5">
                {rallyPoints.map((p, i) => (
                  <li
                    key={i}
                    className="flex items-baseline justify-between gap-2 rounded bg-smoke-900/60 px-2.5 py-1 text-[11px]"
                  >
                    <span className="flex min-w-0 items-baseline gap-1.5">
                      <span
                        className="inline-block h-2 w-2 shrink-0 rounded-full"
                        style={{
                          backgroundColor: windRelationColor(p.wind_relation),
                        }}
                      />
                      <span className="truncate font-medium text-ember-200">
                        {p.name ?? "rally point"}
                      </span>
                      <span className="shrink-0 text-[9px] uppercase tracking-wider text-smoke-500">
                        {(p.rally_type ?? "?").replace(/_/g, " ")}
                      </span>
                    </span>
                    <span className="shrink-0 text-smoke-400">
                      {p.score_raw?.distance_km != null
                        ? `${Number(p.score_raw.distance_km).toFixed(1)} km`
                        : ""}
                      {p.capacity != null && Number(p.capacity) > 0
                        ? ` · ~${Number(p.capacity).toLocaleString()}`
                        : ""}
                      {p.score != null
                        ? ` · ${Number(p.score).toFixed(2)}`
                        : ""}
                    </span>
                  </li>
                ))}
              </ul>
              {rp.rally_source_failures && rp.rally_source_failures.length > 0 && (
                <div className="mt-1 text-[9px] italic text-smoke-500">
                  source failures: {rp.rally_source_failures.join(", ")}
                </div>
              )}
            </div>
          )}
          {candidates.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Top staging candidates
              </div>
              <ul className="space-y-1">
                {candidates.map((c, i) => (
                  <li
                    key={i}
                    className="rounded bg-smoke-900/60 px-2.5 py-1.5 text-[11px]"
                  >
                    <div className="flex items-baseline justify-between">
                      <span className="font-medium text-ember-200">
                        {c.name ?? `candidate ${i + 1}`}
                      </span>
                      <span className="text-smoke-500">
                        score{" "}
                        {typeof c.score === "number"
                          ? c.score.toFixed(2)
                          : "—"}
                      </span>
                    </div>
                    <div className="mt-0.5 text-smoke-400">
                      {c.dist_incident_km != null
                        ? `${c.dist_incident_km.toFixed(1)} km from fire`
                        : ""}
                      {c.nearest_water_km != null
                        ? ` · water ${c.nearest_water_km.toFixed(2)} km`
                        : ""}
                      {c.score_raw?.slope_pct != null
                        ? ` · slope ${Number(c.score_raw.slope_pct).toFixed(1)}%`
                        : ""}
                      {c.tags?.surface ? ` · ${c.tags.surface}` : ""}
                    </div>
                    {c.score_components && (
                      <ScoreBreakdown components={c.score_components} />
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rp.key_findings && <KeyFindings items={rp.key_findings} />}
        </AgentCard>
      </div>
    </div>
  );
}

function deriveFromList(
  items?: string[],
): Array<{ kind?: string; quantity?: number; rationale?: string }> {
  if (!items) return [];
  return items.map((label) => ({ kind: label }));
}

const COMPONENT_LABELS: Array<keyof ScoreComponents> = [
  "incident",
  "water",
  "station",
  "paved",
  "elevation",
  "slope",
  "wind",
];

function ScoreBreakdown({ components }: { components: ScoreComponents }) {
  return (
    <div className="mt-1 grid grid-cols-7 gap-1">
      {COMPONENT_LABELS.map((k) => {
        const v = components[k];
        const pct =
          typeof v === "number" ? Math.max(0, Math.min(100, v * 100)) : 0;
        const color =
          typeof v !== "number"
            ? "#475569"
            : v >= 0.7
              ? "#10b981"
              : v >= 0.4
                ? "#f59e0b"
                : "#dc2626";
        return (
          <div key={k} className="flex flex-col items-stretch gap-0.5">
            <div
              className="h-1 rounded-sm"
              style={{
                background: "#0f172a",
                position: "relative",
                overflow: "hidden",
              }}
              title={`${k}: ${typeof v === "number" ? v.toFixed(2) : "—"}`}
            >
              <div
                style={{
                  height: "100%",
                  width: `${pct}%`,
                  background: color,
                }}
              />
            </div>
            <span className="text-center text-[8px] uppercase tracking-wider text-smoke-500">
              {k.slice(0, 4)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function windRelationColor(rel?: string): string {
  switch (rel) {
    case "upwind":
      return "#10b981";
    case "crosswind":
      return "#f59e0b";
    case "downwind":
      return "#dc2626";
    default:
      return "#94a3b8";
  }
}
