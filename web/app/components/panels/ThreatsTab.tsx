"use client";

import { useStore } from "@/lib/store";
import { AgentCard, KeyFindings, MetricGrid } from "./AgentCard";

type ValuesRollup = {
  structures_total?: number;
  total_footprint_sqm?: number;
  structures_by_occupancy?: Record<string, number>;
  residential_count?: number;
  commercial_count?: number;
  industrial_count?: number;
  hospitals_count?: number;
  schools_count?: number;
  transmission_segments?: number;
  transmission_max_kv?: number;
  critical_facilities_total?: number;
};

type ValuesPayload = {
  rollup?: ValuesRollup;
  key_findings?: string[];
  cone_source?: string;
};

type TerrainPayload = {
  fuel_model?: {
    pixels?: number;
    dominant_classes?: Array<{ name?: string; pct?: number }>;
    purity?: number;
    error?: string;
  };
  terrain?: {
    slope_deg?: Record<string, number>;
    elevation_m?: Record<string, number>;
    error?: string;
  };
  key_findings?: string[];
};

function terrainMetrics(
  tp: TerrainPayload,
): Array<{ label: string; value: string | number; hint?: string }> {
  const out: Array<{ label: string; value: string | number; hint?: string }> = [];
  if (tp.fuel_model?.pixels != null) {
    out.push({
      label: "Fuel pixels",
      value: tp.fuel_model.pixels.toLocaleString(),
      hint:
        tp.fuel_model.purity != null
          ? `purity ${(tp.fuel_model.purity * 100).toFixed(0)}%`
          : undefined,
    });
  }
  if (tp.terrain?.slope_deg?.mean != null) {
    out.push({
      label: "Slope (mean)",
      value: `${Math.round(tp.terrain.slope_deg.mean)}°`,
    });
  }
  if (tp.terrain?.elevation_m?.mean != null) {
    out.push({
      label: "Elevation",
      value: `${Math.round(tp.terrain.elevation_m.mean)} m`,
    });
  }
  return out;
}

export function ThreatsTab() {
  const values = useStore((s) => s.agentOutputs.values_at_risk);
  const valuesStatus = useStore(
    (s) => s.agentStatuses.values_at_risk ?? "pending",
  );
  const terrain = useStore((s) => s.agentOutputs.terrain_fuel);
  const terrainStatus = useStore(
    (s) => s.agentStatuses.terrain_fuel ?? "pending",
  );

  const vp = (values?.payload ?? {}) as ValuesPayload;
  const tp = (terrain?.payload ?? {}) as TerrainPayload;

  const r = vp.rollup ?? {};
  const valueMetrics: { label: string; value: string | number; hint?: string }[] =
    [];
  if (r.structures_total != null)
    valueMetrics.push({
      label: "Structures",
      value: r.structures_total.toLocaleString(),
      hint:
        r.total_footprint_sqm != null
          ? `${Math.round(r.total_footprint_sqm).toLocaleString()} m²`
          : undefined,
    });
  if (r.residential_count != null)
    valueMetrics.push({
      label: "Residential",
      value: r.residential_count.toLocaleString(),
    });
  if (r.hospitals_count != null)
    valueMetrics.push({
      label: "Hospitals",
      value: r.hospitals_count,
    });
  if (r.schools_count != null)
    valueMetrics.push({
      label: "Schools",
      value: r.schools_count,
    });
  if (r.transmission_segments != null)
    valueMetrics.push({
      label: "Transmission",
      value: r.transmission_segments,
      hint:
        r.transmission_max_kv != null
          ? `max ${r.transmission_max_kv} kV`
          : undefined,
    });
  if (r.critical_facilities_total != null)
    valueMetrics.push({
      label: "Critical facilities",
      value: r.critical_facilities_total,
    });

  const occupancy = Object.entries(r.structures_by_occupancy ?? {});

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">
            Threats / Values at Risk
          </h2>
          <p className="text-xs text-smoke-400">
            MS Building Footprints + FEMA USA Structures + HIFLD critical
            infrastructure clipped to the projected spread cone.
            {vp.cone_source ? ` Cone source: ${vp.cone_source}.` : ""}
          </p>
        </header>

        <AgentCard
          title="Values at Risk"
          subtitle="Structures, schools, hospitals, transmission"
          status={valuesStatus}
          output={values}
        >
          <MetricGrid items={valueMetrics} />
          {occupancy.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Structures by occupancy
              </div>
              <ul className="grid grid-cols-2 gap-1 text-[11px] text-smoke-300 sm:grid-cols-3">
                {occupancy
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, v]) => (
                    <li
                      key={k}
                      className="flex items-baseline justify-between rounded bg-smoke-900/60 px-2 py-1"
                    >
                      <span className="text-smoke-400">{k}</span>
                      <span className="text-ember-200">
                        {v.toLocaleString()}
                      </span>
                    </li>
                  ))}
              </ul>
            </div>
          )}
          {vp.key_findings && <KeyFindings items={vp.key_findings} />}
        </AgentCard>

        <AgentCard
          title="Terrain & Fuel"
          subtitle="LANDFIRE FBFM40 · slope · canopy"
          status={terrainStatus}
          output={terrain}
        >
          {tp.fuel_model?.error ? (
            <p className="text-[11px] text-red-300">
              LANDFIRE unavailable: {tp.fuel_model.error}
            </p>
          ) : (
            <MetricGrid items={terrainMetrics(tp)} />
          )}
          {tp.fuel_model?.dominant_classes &&
            tp.fuel_model.dominant_classes.length > 0 && (
              <div>
                <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                  Dominant fuel models
                </div>
                <ul className="space-y-0.5 text-[11px] text-smoke-300">
                  {tp.fuel_model.dominant_classes.slice(0, 5).map((c, i) => (
                    <li key={i}>
                      <span className="text-ember-200">
                        {c.name ?? `class ${i + 1}`}
                      </span>
                      {c.pct != null
                        ? ` — ${(c.pct * 100).toFixed(1)}%`
                        : ""}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          {tp.key_findings && <KeyFindings items={tp.key_findings} />}
        </AgentCard>
      </div>
    </div>
  );
}
