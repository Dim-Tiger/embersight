"use client";

import { useStore } from "@/lib/store";
import { AgentCard, KeyFindings, MetricGrid } from "./AgentCard";

type RoutingPayload = {
  candidates?: Array<{
    name?: string;
    loc?: [number, number];
    score?: number;
    dist_incident_km?: number;
    nearest_water_km?: number;
    tags?: Record<string, string>;
  }>;
  counts?: Record<string, number>;
  key_findings?: string[];
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
                      {c.tags?.surface ? ` · ${c.tags.surface}` : ""}
                    </div>
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
