"use client";

import { useStore } from "@/lib/store";
import { useMemo } from "react";

const AGENT_LABELS: Record<string, string> = {
  orchestrator: "Orchestrator",
  weather_wind: "Weather & Wind",
  terrain_fuel: "Terrain & Fuel",
  values_at_risk: "Values at Risk",
  routing_staging: "Routing & Staging",
  spread_simulation: "Spread Simulation",
  resource_recommendation: "Resource Recommendation",
  evacuation_intelligence: "Evacuation Intelligence",
  master_ic: "Master IC",
};

export function AgentFeed() {
  const events = useStore((s) => s.agentEvents);
  const grouped = useMemo(() => groupByAgent(events), [events]);

  return (
    <section className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <h2 className="text-sm font-semibold text-smoke-200">Agent activity</h2>
        <span className="text-[10px] uppercase tracking-widest text-smoke-400">
          live stream
        </span>
      </header>
      <div className="flex-1 overflow-y-auto">
        {events.length === 0 && (
          <div className="px-4 py-6 text-xs text-smoke-400">
            Select an incident on the map to start an agent run.
          </div>
        )}
        {Object.entries(grouped).map(([agent, evs]) => (
          <div key={agent} className="border-b border-smoke-700/70 px-4 py-3">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-semibold text-ember-200">
                {AGENT_LABELS[agent] ?? agent}
              </span>
              <span className="text-[10px] text-smoke-400">{evs.length}</span>
            </div>
            <ol className="space-y-1">
              {evs.slice(-6).map((e, i) => (
                <li key={i} className="text-[11px] leading-snug text-smoke-200">
                  <span className="mr-2 font-mono text-smoke-400">
                    {e.kind}
                  </span>
                  {summary(e.data)}
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </section>
  );
}

function groupByAgent(
  events: ReturnType<typeof useStore.getState>["agentEvents"],
) {
  const out: Record<string, typeof events> = {};
  for (const e of events) {
    const key = e.name ?? "system";
    (out[key] ??= []).push(e);
  }
  return out;
}

function summary(data: unknown): string {
  if (!data) return "";
  if (typeof data === "string") return truncate(data, 140);
  try {
    return truncate(JSON.stringify(data), 140);
  } catch {
    return "[unserializable]";
  }
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
