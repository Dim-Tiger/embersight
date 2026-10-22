"use client";

import { useStore } from "@/lib/store";
import { AlertTriangle, Flame, Home, Hospital, School, Users, Zap } from "lucide-react";
import { AgentActivityBanner } from "./AgentActivityBanner";
import { AgentCard, KeyFindings } from "./AgentCard";
import { AgentGate, GateNotice } from "@/app/components/ui/AgentGate";
import { AgentStatusRow } from "@/app/components/ui/AgentStatusRow";
import { AsOf, AttentionStrip } from "@/app/components/ui/AttentionStrip";
import { Disclosure, SectionLabel } from "@/app/components/ui/Disclosure";
import { SeverityBadge } from "@/app/components/ui/Severity";
import { StatTile } from "@/app/components/ui/StatTile";
import { useAgentAsOf } from "@/app/components/ui/useAgentAsOf";
import {
  VALUES_SOURCES,
  failedValuesSources,
  flameLengthBand,
  fmtInt,
  fmtNum,
  km2ToAcres,
  terrainDataGap,
  threatAttention,
  topFuelClass,
  type SpreadPayload,
  type TerrainPayload,
  type ValuesPayload,
} from "@/app/components/ui/triage";

const THREAT_AGENTS = ["values_at_risk", "spread_simulation", "terrain_fuel"] as const;

// Turn the backend's cone-source key into words an IC can read.
function coneSourceLabel(src?: string): { text: string; fallback: boolean } | null {
  if (!src) return null;
  if (src.includes("swept_cone_24h"))
    return { text: "24 h spread cone swept over the current perimeter", fallback: false };
  if (src.includes("24h")) return { text: "24 h spread cone", fallback: false };
  if (src.startsWith("default-radius"))
    return {
      text: `fallback circle around the ignition (${src.replace("default-radius-", "")}); no spread cone was available`,
      fallback: true,
    };
  return { text: src, fallback: false };
}

// "0 hospitals (0 beds)" style findings say nothing the tiles don't already
// show, and read like an all-clear. Keep them out of the findings list.
function nonZeroFindings(items?: string[]): string[] {
  return (items ?? []).filter((f) => !/^0\s/.test(f.trim()));
}

export function ThreatsTab() {
  const values = useStore((s) => s.agentOutputs.values_at_risk);
  const valuesStatus = useStore((s) => s.agentStatuses.values_at_risk ?? "pending");
  const terrain = useStore((s) => s.agentOutputs.terrain_fuel);
  const terrainStatus = useStore((s) => s.agentStatuses.terrain_fuel ?? "pending");
  const spread = useStore((s) => s.agentOutputs.spread_simulation);
  const spreadStatus = useStore((s) => s.agentStatuses.spread_simulation ?? "pending");
  const asOf = useAgentAsOf(THREAT_AGENTS);

  const vp = (values?.payload ?? {}) as ValuesPayload;
  const tp = (terrain?.payload ?? {}) as TerrainPayload;
  const sp = (spread?.payload ?? {}) as SpreadPayload;
  const r = vp.rollup ?? {};
  const failed = new Set(failedValuesSources(vp));
  const failMsg = (key: string) =>
    failed.has(key) ? `${VALUES_SOURCES[key]} source failed on this run` : null;

  const attention = threatAttention({ values, spread, terrain });
  const cone = coneSourceLabel(vp.cone_source);
  const occupancy = Object.entries(r.structures_by_occupancy ?? {}).sort(
    (a, b) => b[1] - a[1],
  );
  const impact = sp.cone_impact && !sp.cone_impact.error ? sp.cone_impact : null;

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-5">
        <header className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h2 className="text-xl font-semibold text-smoke-100">
              Threats / Values at Risk
            </h2>
            <AsOf ts={asOf} />
          </div>
          <AgentStatusRow
            agents={THREAT_AGENTS}
            gaps={{
              values_at_risk: failed.size ? `${failed.size} of 6 data sources failed` : null,
              terrain_fuel: terrain ? terrainDataGap(tp) : null,
              spread_simulation: sp.cone_impact?.error ?? null,
            }}
          />
        </header>

        <AttentionStrip
          items={attention}
          emptyText="No threats flagged yet. Items appear here as Spread, Values at Risk and Terrain report."
        />

        {/* ---------------- Exposure ---------------- */}
        <section className="space-y-3 rounded-md border border-smoke-700 bg-smoke-800/40 p-4">
          <SectionLabel right="Values at Risk agent">
            Exposure inside the 24 h projected cone
          </SectionLabel>
          <AgentGate
            agent="values_at_risk"
            fallbackHint="Use the county parcel layer for structure counts until it reports."
          >
            {cone && (
              <p className="flex flex-wrap items-center gap-2 text-[12px] text-smoke-300">
                {cone.fallback && <SeverityBadge level="degraded" word="Fallback area" />}
                Area counted: {cone.text}.
              </p>
            )}
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
              <StatTile
                label="People (est.)"
                icon={<Users className="h-3.5 w-3.5" aria-hidden />}
                value={impact?.population_estimate != null ? `~${fmtInt(impact.population_estimate)}` : "—"}
                unit="people"
                // Same number the Spread report quotes. The agent takes the
                // larger of homes × 2.5 people and a building-density model.
                hint="Spread agent estimate: homes × 2.5 or building density, whichever is larger"
                unavailable={impact ? null : spread ? "Spread exposure query unavailable" : "Waiting for Spread Simulation"}
              />
              <StatTile
                label="Structures"
                icon={<Home className="h-3.5 w-3.5" aria-hidden />}
                value={fmtInt(r.structures_total)}
                unit="buildings"
                level={(r.structures_total ?? 0) > 0 ? "warning" : undefined}
                hint={
                  <>
                    {failed.has("usa_structures")
                      ? "Homes: unknown (occupancy source failed)"
                      : `${fmtInt(r.residential_count)} homes`}
                    {r.total_footprint_sqm
                      ? ` · ${fmtInt(r.total_footprint_sqm)} m² footprint`
                      : ""}
                  </>
                }
                unavailable={failMsg("ms_buildings")}
              />
              <StatTile
                label="Hospitals"
                icon={<Hospital className="h-3.5 w-3.5" aria-hidden />}
                value={fmtInt(r.hospitals_count)}
                unit={r.hospitals_count === 1 ? "hospital" : "hospitals"}
                level={(r.hospitals_count ?? 0) > 0 ? "critical" : undefined}
                hint={
                  (r.hospitals_count ?? 0) > 0
                    ? `${fmtInt(r.hospitals_total_beds)} beds`
                    : "None found in cone"
                }
                unavailable={failMsg("hospitals")}
              />
              <StatTile
                label="Schools"
                icon={<School className="h-3.5 w-3.5" aria-hidden />}
                value={fmtInt(r.schools_count)}
                unit={r.schools_count === 1 ? "school" : "schools"}
                level={(r.schools_count ?? 0) > 0 ? "warning" : undefined}
                hint={
                  (r.schools_count ?? 0) > 0
                    ? `${fmtInt(r.schools_total_enrollment)} enrolled`
                    : "None found in cone"
                }
                unavailable={failMsg("schools")}
              />
              <StatTile
                label="Transmission"
                icon={<Zap className="h-3.5 w-3.5" aria-hidden />}
                value={fmtInt(r.transmission_segments)}
                unit="line segments"
                level={(r.transmission_segments ?? 0) > 0 ? "warning" : undefined}
                hint={
                  (r.transmission_segments ?? 0) > 0
                    ? `Up to ${fmtInt(r.transmission_max_kv)} kV`
                    : "None found in cone"
                }
                unavailable={failMsg("transmission")}
              />
              <StatTile
                label="Critical facilities"
                icon={<AlertTriangle className="h-3.5 w-3.5" aria-hidden />}
                value={fmtInt(r.critical_facilities_total)}
                unit="facilities"
                level={(r.critical_facilities_total ?? 0) > 0 ? "warning" : undefined}
                hint={(r.critical_facilities_total ?? 0) > 0 ? "HIFLD critical infrastructure" : "None found in cone"}
                unavailable={failMsg("critical_facilities")}
              />
            </div>

            {((r.named_hospitals?.length ?? 0) > 0 || (r.named_schools?.length ?? 0) > 0) && (
              <div className="grid gap-3 md:grid-cols-2">
                {(r.named_hospitals?.length ?? 0) > 0 && (
                  <div>
                    <SectionLabel>Hospitals by beds</SectionLabel>
                    <NamedList
                      rows={(r.named_hospitals ?? []).map((h) => [h.name ?? "Unknown", `${fmtInt(h.beds)} beds`])}
                    />
                  </div>
                )}
                {(r.named_schools?.length ?? 0) > 0 && (
                  <div>
                    <SectionLabel>Schools by enrollment</SectionLabel>
                    <NamedList
                      rows={(r.named_schools ?? []).map((s) => [s.name ?? "Unknown", `${fmtInt(s.enrollment)} students`])}
                    />
                  </div>
                )}
              </div>
            )}

            {occupancy.length > 0 && (
              <Disclosure tone="plain" title="Structures by occupancy" meta={`${occupancy.length} types`}>
                <ul className="grid grid-cols-2 gap-1 text-[12px] sm:grid-cols-3">
                  {occupancy.map(([k, v]) => (
                    <li
                      key={k}
                      className="flex items-baseline justify-between rounded bg-smoke-900/60 px-2 py-1"
                    >
                      <span className="text-smoke-300">{k}</span>
                      <span className="font-semibold tabular-nums text-smoke-100">
                        {v.toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </Disclosure>
            )}
          </AgentGate>
        </section>

        {/* ---------------- Fire behaviour ---------------- */}
        <section className="space-y-3 rounded-md border border-smoke-700 bg-smoke-800/40 p-4">
          <SectionLabel right="Spread Simulation agent">Fire behaviour at the head</SectionLabel>
          <AgentGate
            agent="spread_simulation"
            fallbackHint="Use the FBAN or field observations for rate of spread."
          >
            <SpreadSection sp={sp} />
          </AgentGate>
        </section>

        {/* ---------------- Terrain & fuel ---------------- */}
        <section className="space-y-3 rounded-md border border-smoke-700 bg-smoke-800/40 p-4">
          <SectionLabel right="Terrain & Fuel agent · LANDFIRE FBFM40">Terrain &amp; fuel</SectionLabel>
          <AgentGate agent="terrain_fuel" fallbackHint="Verify fuel model and slope in the field.">
            <TerrainSection tp={tp} />
          </AgentGate>
        </section>

        {/* ---------------- Full reports ---------------- */}
        <Disclosure
          title="Full agent reports"
          meta="narratives · key findings · confidence drivers · citations"
        >
          <AgentCard
            title="Values at Risk"
            subtitle="MS Building Footprints + FEMA USA Structures + HIFLD critical infrastructure clipped to the projected spread cone"
            status={valuesStatus}
            output={values}
          >
            {vp.cone_source && (
              <p className="text-[11px] text-smoke-400">Cone source: {vp.cone_source}</p>
            )}
            {failed.size > 0 && (
              <p className="text-[12px] text-smoke-200">
                <SeverityBadge level="degraded" /> Failed sources:{" "}
                {[...failed].map((k) => VALUES_SOURCES[k]).join(", ")}
              </p>
            )}
            {vp.key_findings && <KeyFindings items={nonZeroFindings(vp.key_findings)} />}
          </AgentCard>
          <AgentCard
            title="Spread Simulation"
            subtitle="Rothermel ROS · Monte Carlo cone"
            status={spreadStatus}
            output={spread}
          >
            {sp.key_findings && <KeyFindings items={sp.key_findings} />}
          </AgentCard>
          <AgentCard
            title="Terrain & Fuel"
            subtitle="LANDFIRE FBFM40 · slope · canopy"
            status={terrainStatus}
            output={terrain}
          >
            {tp.key_findings && <KeyFindings items={tp.key_findings} />}
          </AgentCard>
        </Disclosure>

        <Disclosure title="Agent activity log" meta="live event ticker">
          <AgentActivityBanner
            title="Threat Analysis"
            subtitle="Values · Terrain · Spread"
            agents={["values_at_risk", "terrain_fuel", "spread_simulation"]}
            icon={<AlertTriangle className="h-5 w-5" />}
          />
        </Disclosure>
      </div>
    </div>
  );
}

function NamedList({ rows }: { rows: Array<[string, string]> }) {
  return (
    <ol className="space-y-1 text-[12.5px]">
      {rows.map(([name, value], i) => (
        <li
          key={`${name}-${i}`}
          className="flex items-baseline justify-between gap-2 rounded bg-smoke-900/60 px-2.5 py-1.5"
        >
          <span className="truncate font-medium text-smoke-100">{name}</span>
          <span className="shrink-0 tabular-nums text-smoke-300">{value}</span>
        </li>
      ))}
    </ol>
  );
}

function SpreadSection({ sp }: { sp: SpreadPayload }) {
  const fl = sp.flame_length_ft;
  const band = typeof fl === "number" && fl > 0 ? flameLengthBand(fl) : null;
  const ros = sp.head_ros_chains_per_hr;
  const area = sp.burn_area_24h_km2_p25;
  const zones = [...(sp.high_risk_zones ?? [])].sort(
    (a, b) => (a.horizon_h ?? 0) - (b.horizon_h ?? 0),
  );
  const breaches = sp.trigger_breaches ?? [];

  if (fl == null && ros == null && area == null) {
    return (
      <GateNotice tone="degraded">
        Spread model returned no fire behaviour this run.
        {sp.key_findings?.[0] ? ` Reason: ${sp.key_findings[0]}` : ""}
      </GateNotice>
    );
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <StatTile
          label="Flame length"
          icon={<Flame className="h-3.5 w-3.5" aria-hidden />}
          value={fmtNum(fl ?? null, 0)}
          unit="ft"
          level={band?.level}
          hint={band?.meaning}
        />
        <StatTile
          label="Head rate of spread"
          value={fmtNum(ros ?? null, 1)}
          unit="chains/h"
          hint={
            <>
              {typeof ros === "number" ? `${fmtNum((ros * 66) / 5280, 2)} mph` : ""}
              {sp.head_ros_fpm_mean != null
                ? ` · ${fmtNum(sp.head_ros_fpm_mean, 1)} ± ${fmtNum(sp.head_ros_fpm_std ?? 0, 1)} ft/min`
                : ""}
            </>
          }
        />
        <StatTile
          label="24 h burn area"
          value={fmtNum(area ?? null, 1)}
          unit="km²"
          hint={
            typeof area === "number"
              ? `≈ ${fmtInt(km2ToAcres(area))} acres · at least 25% probability (p25)`
              : "Not reported"
          }
        />
      </div>

      {breaches.length > 0 && (
        <div>
          <SectionLabel>Trigger points projected to be breached</SectionLabel>
          <ul className="space-y-1">
            {breaches.map((b, i) => (
              <li
                key={`${b.trigger_id}-${i}`}
                className="flex flex-wrap items-center gap-2 rounded bg-smoke-900/60 px-2.5 py-1.5 text-[13px] text-smoke-100"
              >
                <SeverityBadge level="critical" word="Breach" />
                <span className="font-semibold">{b.trigger_id ?? "Trigger point"}</span>
                <span className="text-smoke-300">
                  in {fmtNum(b.hours_until_breach ?? null, 0)} h
                  {b.prob_at_breach != null ? ` · ${Math.round(b.prob_at_breach * 100)}% probability` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {zones.length > 0 && (
        <div>
          <SectionLabel right="where the fire is most likely to be (p75 band)">
            Projected high-risk sectors
          </SectionLabel>
          <table className="w-full text-left text-[12.5px]">
            <thead className="text-[11px] uppercase tracking-wider text-smoke-400">
              <tr>
                <th className="py-1 pr-3 font-semibold">In</th>
                <th className="py-1 pr-3 font-semibold">Direction</th>
                <th className="py-1 pr-3 font-semibold">Distance</th>
                <th className="py-1 font-semibold">Area</th>
              </tr>
            </thead>
            <tbody className="text-smoke-100">
              {zones.map((z, i) => (
                <tr key={z.id ?? i} className="border-t border-smoke-700/70">
                  <td className="py-1.5 pr-3 font-semibold tabular-nums">{z.horizon_h} h</td>
                  <td className="py-1.5 pr-3">
                    {z.sector}
                    {z.bearing_deg != null && (
                      <span className="text-smoke-400"> ({fmtNum(z.bearing_deg, 0)}°)</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 tabular-nums">{fmtNum(z.distance_km ?? null, 1)} km</td>
                  <td className="py-1.5 tabular-nums">{fmtNum(z.area_km2 ?? null, 1)} km²</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-smoke-400">
        {sp.n_mc_samples ? `Monte Carlo, ${sp.n_mc_samples} runs. ` : ""}
        {sp.fuel_model ? `Fuel model ${sp.fuel_model}. ` : ""}
        {sp.wind_speed_mph != null ? `Wind ${fmtNum(sp.wind_speed_mph, 0)} mph` : ""}
        {sp.wind_dir_deg != null ? ` from ${fmtNum(sp.wind_dir_deg, 0)}°.` : ""}
      </p>
    </>
  );
}

function TerrainSection({ tp }: { tp: TerrainPayload }) {
  const gap = terrainDataGap(tp);
  const top = topFuelClass(tp);
  const slope = tp.terrain?.slope_deg ?? {};
  const elev = tp.terrain?.elevation_m ?? {};
  const aspect = Object.entries(tp.terrain?.aspect_distribution ?? {}).sort((a, b) => b[1] - a[1])[0];
  const canopy = tp.canopy;
  // Older payloads put the class detail under `fuel_model` (object), newer
  // ones under `fuel_detail`; accept both.
  const legacy = typeof tp.fuel_model === "object" ? tp.fuel_model : null;
  const classes = tp.fuel_detail?.dominant_classes ?? [];

  return (
    <>
      {gap && (
        <GateNotice tone="degraded">
          <SeverityBadge level="degraded" /> {gap}
        </GateNotice>
      )}
      {legacy?.error && (
        <GateNotice tone="degraded">LANDFIRE unavailable: {legacy.error}</GateNotice>
      )}
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <StatTile
          label="Dominant fuel"
          value={top && !top.nodata ? top.code : typeof tp.fuel_model === "string" ? tp.fuel_model : "—"}
          hint={
            top?.fraction != null
              ? `${fmtNum(top.fraction * 100, 0)}% of the ${fmtInt(tp.aoi_radius_km)} km area`
              : undefined
          }
          unavailable={top?.nodata ? "LANDFIRE returned no-data pixels" : null}
        />
        <StatTile
          label="Slope"
          value={slope.mean != null ? fmtNum(slope.mean, 0) : "—"}
          unit="° mean"
          level={(slope.p90 ?? 0) >= 30 ? "warning" : undefined}
          hint={slope.p90 != null ? `Steepest 10%: ${fmtNum(slope.p90, 0)}° or more` : undefined}
        />
        <StatTile
          label="Elevation"
          value={elev.mean != null ? fmtInt(elev.mean) : "—"}
          unit="m mean"
          hint={aspect ? `Dominant aspect ${aspect[0]} (${fmtNum(aspect[1] * 100, 0)}%)` : undefined}
        />
        <StatTile
          label="Canopy cover"
          value={canopy?.mean_pct != null ? fmtNum(canopy.mean_pct, 0) : "—"}
          unit="% mean"
          hint={
            canopy?.p10_pct != null && canopy?.p90_pct != null
              ? `Range ${fmtNum(canopy.p10_pct, 0)}–${fmtNum(canopy.p90_pct, 0)}% (p10–p90)`
              : undefined
          }
        />
      </div>
      {classes.length > 0 && (
        <Disclosure
          tone="plain"
          title="Fuel model mix"
          meta={
            tp.fuel_detail?.purity != null
              ? `purity ${fmtNum(tp.fuel_detail.purity * 100, 0)}% · ${fmtInt(tp.fuel_detail.pixels)} pixels`
              : undefined
          }
        >
          <ul className="space-y-1 text-[12.5px]">
            {classes.slice(0, 5).map((c, i) => {
              const frac = typeof c.fraction === "number" ? c.fraction : c.pct;
              return (
                <li key={i} className="flex items-baseline justify-between rounded bg-smoke-900/60 px-2.5 py-1">
                  <span className="text-smoke-100">{c.code ?? c.name ?? `class ${i + 1}`}</span>
                  <span className="tabular-nums text-smoke-300">
                    {frac != null ? `${fmtNum(frac * 100, 1)}%` : ""}
                  </span>
                </li>
              );
            })}
          </ul>
        </Disclosure>
      )}
    </>
  );
}
