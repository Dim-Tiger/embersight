"use client";

import { useStore } from "@/lib/store";
import { Wind } from "lucide-react";
import { AgentActivityBanner } from "./AgentActivityBanner";
import { AgentCard, MetricGrid } from "./AgentCard";

type RedFlag = {
  event?: string;
  headline?: string;
  source?: string;
  rh_pct?: number;
  wind_speed_mph?: number;
  temp_f?: number;
};

type CriticalWindow = {
  valid_time?: string;
  wind_speed_mph?: number;
  wind_direction_deg?: number;
  rh_pct?: number;
  temp_f?: number;
};

type RawsSummary = {
  station_count?: number;
  stations?: Array<{ stid?: string; name?: string }>;
  error?: string;
};

export function WeatherTab() {
  const output = useStore((s) => s.agentOutputs.weather_wind);
  const status = useStore((s) => s.agentStatuses.weather_wind ?? "pending");

  const payload = (output?.payload ?? {}) as {
    red_flag?: RedFlag | null;
    critical_window?: CriticalWindow | null;
    raws_summary?: RawsSummary;
    nws_alerts?: Array<{
      event?: string;
      severity?: string;
      headline?: string;
      url?: string;
    }>;
    hrrr_hourly?: Array<unknown>;
    rtma_now?: Record<string, number | string | null>;
  };

  const redFlag = payload.red_flag ?? null;
  const critical = payload.critical_window ?? null;
  const raws = payload.raws_summary ?? {};
  const alerts = payload.nws_alerts ?? [];

  const metrics: { label: string; value: string | number; hint?: string }[] = [];
  if (critical) {
    if (critical.wind_speed_mph != null)
      metrics.push({
        label: "Peak wind",
        value: `${Math.round(Number(critical.wind_speed_mph))} mph`,
        hint:
          critical.wind_direction_deg != null
            ? `from ${Math.round(Number(critical.wind_direction_deg))}°`
            : undefined,
      });
    if (critical.rh_pct != null)
      metrics.push({
        label: "Min RH",
        value: `${Math.round(Number(critical.rh_pct))}%`,
        hint: critical.valid_time
          ? `at ${new Date(critical.valid_time).toLocaleString()}`
          : undefined,
      });
    if (critical.temp_f != null)
      metrics.push({
        label: "Temp",
        value: `${Math.round(Number(critical.temp_f))} °F`,
      });
  }
  if (raws.station_count != null) {
    metrics.push({
      label: "RAWS stations",
      value: raws.station_count,
      hint: raws.error || undefined,
    });
  }
  if (payload.hrrr_hourly) {
    metrics.push({
      label: "HRRR hours",
      value: payload.hrrr_hourly.length,
    });
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">Weather</h2>
          <p className="text-xs text-smoke-400">
            FBAN-style fusion of NWS alerts, HRRR forecast, RTMA nowcast, and
            nearby RAWS observations — produced by the Weather &amp; Wind agent.
          </p>
        </header>

        <AgentActivityBanner
          title="Weather Intelligence"
          subtitle="HRRR · RTMA · RAWS · NWS"
          agents={["weather_wind"]}
          icon={<Wind className="h-5 w-5" />}
        />

        {redFlag && (
          <div className="rounded border border-red-700/60 bg-red-900/30 px-3 py-2 text-xs text-red-200">
            <div className="font-semibold">
              🚩 {redFlag.event ?? "Red Flag conditions"}
            </div>
            {redFlag.headline && (
              <div className="mt-0.5 text-[11px]">{redFlag.headline}</div>
            )}
            {redFlag.source && (
              <div className="mt-0.5 text-[10px] text-red-300">
                source: {redFlag.source}
                {redFlag.rh_pct != null
                  ? ` · RH ${redFlag.rh_pct}% · ${redFlag.wind_speed_mph} mph · ${redFlag.temp_f}°F`
                  : ""}
              </div>
            )}
          </div>
        )}

        <AgentCard
          title="Weather & Wind"
          subtitle="HRRR + RTMA + RAWS + NWS alerts"
          status={status}
          output={output}
        >
          <MetricGrid items={metrics} />
          {alerts.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Active NWS alerts
              </div>
              <ul className="space-y-1 text-[11px] text-smoke-300">
                {alerts.map((a, i) => (
                  <li key={i} className="rounded bg-smoke-900/60 px-2 py-1">
                    <div className="text-ember-200">{a.event}</div>
                    {a.headline && (
                      <div className="text-smoke-400">{a.headline}</div>
                    )}
                    {a.severity && (
                      <div className="text-[10px] text-smoke-500">
                        severity {a.severity}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {raws.stations && raws.stations.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Nearby RAWS
              </div>
              <ul className="grid grid-cols-2 gap-1 text-[11px] text-smoke-300 sm:grid-cols-3">
                {raws.stations.slice(0, 9).map((s, i) => (
                  <li
                    key={i}
                    className="truncate rounded bg-smoke-900/60 px-2 py-1"
                  >
                    <span className="text-ember-300">{s.stid}</span>{" "}
                    <span className="text-smoke-500">{s.name}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </AgentCard>
      </div>
    </div>
  );
}
