"use client";

import { useEvacZones, useIncidents } from "@/lib/queries";
import { useStore } from "@/lib/store";
import { haversineKm, kmToMi } from "@/lib/units";
import { Route } from "lucide-react";
import { useMemo } from "react";
import { AgentActivityBanner } from "./AgentActivityBanner";
import { AgentCard, KeyFindings } from "./AgentCard";

// Zones farther than this from the incident are summarised as a statewide
// count instead of being listed as if they belonged to this fire.
const NEARBY_KM = 40; // ~25 mi

type EgressStatus = {
  clear?: boolean | null;
  reason?: string;
  egress_edges_blocked?: number;
};

// One entry in zones_order / zones_advisory / zones_watch. Older agent
// builds sent bare zone-id strings, so both shapes are accepted.
type PhaseEntry =
  | string
  | {
      zone_id?: string;
      name?: string;
      current_status?: string;
      proposed_phase?: string;
      jurisdiction?: string | null;
      population_estimate?: number;
      egress_status?: EgressStatus;
    };

type ZoneChange = {
  zone_id?: string;
  name?: string;
  current_status?: string;
  proposed_status?: string;
  final_status?: string;
  decision?: { decision?: string } | null;
  rationale?: string;
  population_estimate?: number;
};

type EvacPayload = {
  zones_advisory?: PhaseEntry[];
  zones_watch?: PhaseEntry[];
  zones_order?: PhaseEntry[];
  zone_changes?: ZoneChange[];
  zones_fetched?: number;
  routes?: Array<{ name?: string; status?: string }>;
  key_findings?: string[];
};

type LiveZone = {
  id: string;
  status: string;
  county: string | null;
  notes: string | null;
  edited: number | null;
  km: number | null;
};

export function EvacuationTab() {
  const { data, isLoading, error } = useEvacZones();
  const evac = useStore((s) => s.agentOutputs.evacuation_intelligence);
  const evacStatus = useStore(
    (s) => s.agentStatuses.evacuation_intelligence ?? "pending",
  );
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const { data: incidents } = useIncidents();
  const incident = incidents?.find((i) => i.id === selectedIncidentId);

  const ep = (evac?.payload ?? {}) as EvacPayload;
  const zones = useMemo<LiveZone[]>(() => {
    const features = (data?.features ?? []) as Array<{
      properties?: Record<string, unknown>;
      geometry?: GeoJSON.Geometry | null;
    }>;
    return features
      .map((f) => {
        const p = f.properties ?? {};
        return {
          id: String(p.ZONE_NAME ?? p.ZONE_ID ?? p.OBJECTID ?? "zone"),
          status: String(p.STATUS ?? p.status ?? "Unknown"),
          county: p.COUNTY ? String(p.COUNTY) : null,
          notes: p.NOTES ? String(p.NOTES) : null,
          edited: typeof p.EDIT_DATE === "number" ? p.EDIT_DATE : null,
          km: incident
            ? nearestVertexKm(f.geometry ?? null, incident.lat, incident.lon)
            : null,
        };
      })
      .sort((a, b) => (a.km ?? Infinity) - (b.km ?? Infinity));
  }, [data, incident]);

  const nearby = zones.filter((z) => z.km != null && z.km <= NEARBY_KM);
  const farCount = zones.length - nearby.length;
  const nearestFar = zones.find((z) => z.km != null && z.km > NEARBY_KM);

  const phaseCount =
    (ep.zones_order?.length ?? 0) +
    (ep.zones_advisory?.length ?? 0) +
    (ep.zones_watch?.length ?? 0);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">Evacuation</h2>
          <p className="text-xs text-smoke-400">
            Live Cal OES CA_EVACUATIONS feed + recommendations from the
            Evacuation Intelligence agent. EmberSight only{" "}
            <span className="text-ember-200">PROPOSES</span> status changes.
          </p>
        </header>

        <AgentActivityBanner
          title="Evacuation Intelligence"
          subtitle="Zone phasing · egress routes"
          agents={["evacuation_intelligence"]}
          icon={<Route className="h-5 w-5" />}
        />

        <AgentCard
          title="Evacuation Intelligence"
          subtitle="Phasing proposals and route status"
          status={evacStatus}
          output={evac}
        >
          {phaseCount > 0 && (
            <div className="grid grid-cols-1 gap-2 text-[11px] sm:grid-cols-3">
              <ZoneCol
                title="PROPOSED order"
                tone="red"
                items={ep.zones_order ?? []}
              />
              <ZoneCol
                title="PROPOSED advisory"
                tone="amber"
                items={ep.zones_advisory ?? []}
              />
              <ZoneCol
                title="PROPOSED watch"
                tone="ember"
                items={ep.zones_watch ?? []}
              />
            </div>
          )}
          {ep.zone_changes && ep.zone_changes.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Zones reviewed · {ep.zone_changes.length}
              </div>
              <ul className="space-y-1 text-[11px] text-smoke-300">
                {ep.zone_changes.map((z, i) => (
                  <li
                    key={z.zone_id ?? i}
                    className="rounded border border-smoke-700 bg-smoke-900/60 px-2 py-1"
                  >
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-medium text-smoke-200">
                        {z.name ?? z.zone_id}
                      </span>
                      <span className="text-smoke-400">
                        {z.current_status ?? "?"} → {z.proposed_status ?? "?"}
                        {z.decision?.decision
                          ? ` · ${z.decision.decision}`
                          : ""}
                      </span>
                      {z.population_estimate != null && (
                        <span className="text-smoke-400">
                          ~{Math.round(z.population_estimate).toLocaleString()}{" "}
                          people
                        </span>
                      )}
                    </div>
                    {z.rationale && (
                      <div className="text-smoke-400">{z.rationale}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ep.routes && ep.routes.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Egress routes
              </div>
              <ul className="space-y-0.5 text-[11px] text-smoke-300">
                {ep.routes.map((r, i) => (
                  <li key={i}>
                    <span className="text-ember-200">{r.name}</span>
                    {r.status ? (
                      <span className="text-smoke-400"> — {r.status}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ep.key_findings && <KeyFindings items={ep.key_findings} />}
        </AgentCard>

        <section className="rounded-md border border-smoke-700 bg-smoke-800/40">
          <header className="flex items-center justify-between gap-3 border-b border-smoke-700 px-4 py-2">
            <div>
              <h2 className="text-sm font-semibold text-smoke-100">
                Live Cal OES zones near {incident?.name ?? "this incident"}
              </h2>
              <p className="text-[10px] text-smoke-400">
                Within {Math.round(kmToMi(NEARBY_KM))} mi of the incident
                point · CA_EVACUATIONS (Zonehaven schema), refreshed every 5
                min
              </p>
            </div>
            <span className="whitespace-nowrap rounded bg-smoke-800 px-2 py-0.5 text-[10px] text-smoke-300">
              {nearby.length} nearby · {zones.length} statewide
            </span>
          </header>
          <div className="space-y-2 p-4">
            {isLoading && (
              <div className="text-xs text-smoke-400">
                Loading Cal OES evac zones…
              </div>
            )}
            {error && (
              <div className="text-xs text-red-300">
                Failed to load evac zones: {String(error)}
              </div>
            )}
            {!isLoading && !error && nearby.length === 0 && (
              <div className="text-xs text-smoke-300">
                No Cal OES evacuation zones are active within{" "}
                {Math.round(kmToMi(NEARBY_KM))} mi of this incident.
              </div>
            )}
            {nearby.length > 0 && (
              <ul className="space-y-1">
                {nearby.map((z) => (
                  <LiveZoneRow key={z.id} zone={z} />
                ))}
              </ul>
            )}
            {farCount > 0 && (
              <p className="text-[11px] text-smoke-400">
                {farCount} other active zone{farCount === 1 ? "" : "s"}{" "}
                statewide
                {nearestFar?.km != null
                  ? `, the nearest ${Math.round(kmToMi(nearestFar.km))} mi away (${nearestFar.county ?? "unknown county"}${nearestFar.notes ? `, ${nearestFar.notes}` : ""})`
                  : ""}
                .
              </p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function LiveZoneRow({ zone }: { zone: LiveZone }) {
  const isOrder = /order/i.test(zone.status);
  return (
    <li className="flex flex-wrap items-baseline gap-x-3 rounded border border-smoke-700 bg-smoke-900/60 px-3 py-1.5 text-[11px]">
      <span
        className={`font-semibold uppercase tracking-wide ${
          isOrder ? "text-red-300" : "text-amber-300"
        }`}
      >
        {zone.status}
      </span>
      <span className="text-smoke-200">{zone.id}</span>
      {zone.county && <span className="text-smoke-400">{zone.county}</span>}
      {zone.notes && <span className="text-smoke-400">{zone.notes}</span>}
      {zone.km != null && (
        <span className="ml-auto text-smoke-300">
          {zone.km < 0.5 ? "at the incident" : `${kmToMi(zone.km).toFixed(1)} mi`}
        </span>
      )}
      {zone.edited != null && (
        <span className="w-full text-[10px] text-smoke-400">
          last edited {new Date(zone.edited).toLocaleDateString()}
        </span>
      )}
    </li>
  );
}

function ZoneCol({
  title,
  items,
  tone,
}: {
  title: string;
  items: PhaseEntry[];
  tone: "red" | "amber" | "ember";
}) {
  const toneClass =
    tone === "red"
      ? "text-red-300"
      : tone === "amber"
        ? "text-amber-300"
        : "text-ember-300";
  return (
    <div className="rounded border border-smoke-700 bg-smoke-900/60 p-2">
      <div className={`text-[10px] font-semibold uppercase ${toneClass}`}>
        {title} · {items.length}
      </div>
      <ul className="mt-1 space-y-1 text-smoke-300">
        {items.length === 0 ? (
          <li className="italic text-smoke-400">none</li>
        ) : (
          items.map((z, i) =>
            typeof z === "string" ? (
              <li key={z}>{z}</li>
            ) : (
              <li key={z.zone_id ?? i}>
                <div className="text-smoke-200">{z.name ?? z.zone_id}</div>
                <div className="text-[10px] text-smoke-400">
                  now {z.current_status ?? "?"}
                  {z.population_estimate != null
                    ? ` · ~${Math.round(z.population_estimate).toLocaleString()} people`
                    : ""}
                  {z.egress_status
                    ? ` · egress ${
                        z.egress_status.clear == null
                          ? `unknown (${z.egress_status.reason ?? "no data"})`
                          : z.egress_status.clear
                            ? "clear"
                            : "at risk"
                      }`
                    : ""}
                </div>
              </li>
            ),
          )
        )}
      </ul>
    </div>
  );
}

/** Distance from (lat, lon) to the closest vertex of a zone polygon. */
function nearestVertexKm(
  geom: GeoJSON.Geometry | null,
  lat: number,
  lon: number,
): number | null {
  if (!geom) return null;
  let best = Infinity;
  const visit = (c: unknown): void => {
    if (!Array.isArray(c)) return;
    if (typeof c[0] === "number" && typeof c[1] === "number") {
      best = Math.min(best, haversineKm(lat, lon, c[1] as number, c[0] as number));
      return;
    }
    for (const x of c) visit(x);
  };
  if ("coordinates" in geom) visit(geom.coordinates);
  return Number.isFinite(best) ? best : null;
}
