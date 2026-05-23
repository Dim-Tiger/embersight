"use client";

import { useStore, type AgentEvent } from "@/lib/store";
import { RefreshCw } from "lucide-react";
import { useMemo } from "react";

const AGENT_ORDER = [
  "orchestrator",
  "weather_wind",
  "terrain_fuel",
  "values_at_risk",
  "routing_staging",
  "spread_simulation",
  "resource_recommendation",
  "evacuation_intelligence",
  "master_ic",
] as const;

const AGENT_LABELS: Record<string, string> = {
  orchestrator: "Orchestrator",
  weather_wind: "Weather & Wind",
  terrain_fuel: "Terrain & Fuel",
  values_at_risk: "Values at Risk",
  routing_staging: "Routing & Staging",
  spread_simulation: "Spread Simulation",
  resource_recommendation: "Resources",
  evacuation_intelligence: "Evacuation Intel",
  master_ic: "Master IC",
};

type AgentStatus = "pending" | "running" | "done" | "error";

type AgentInfo = {
  status: AgentStatus;
  narrative: string | null;
  confidence: number | null;
};

function deriveAgentInfo(events: AgentEvent[]): Record<string, AgentInfo> {
  const out: Record<string, AgentInfo> = {};
  for (const a of AGENT_ORDER) {
    out[a] = { status: "pending", narrative: null, confidence: null };
  }
  for (const e of events) {
    if (e.kind !== "agent-event") continue;
    const inner = e.data as Record<string, unknown> | null;
    if (!inner) continue;
    const innerKind = inner.kind as string | undefined;
    const name = (inner.name as string) ?? e.name;
    if (!name || !(name in out)) continue;

    if (innerKind === "on_chain_start" && out[name].status === "pending") {
      out[name] = { ...out[name], status: "running" };
    } else if (innerKind === "on_chain_end") {
      const outputBlock = (inner.data as any)?.output?.outputs?.[name];
      out[name] = {
        status: "done",
        narrative: outputBlock?.narrative ?? null,
        confidence:
          typeof outputBlock?.confidence === "number"
            ? outputBlock.confidence
            : null,
      };
    }
  }
  return out;
}

export function AgentFeed() {
  const events = useStore((s) => s.agentEvents);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const requestRestart = useStore((s) => s.requestRestart);

  const agentInfo = useMemo(() => deriveAgentInfo(events), [events]);

  const hasStart = events.some((e) => e.kind === "start");
  const hasDone = events.some((e) => e.kind === "done");
  const hasError = events.some((e) => e.kind === "error");
  const isStreaming = hasStart && !hasDone && !hasError;
  const isConnecting = !!selectedIncidentId && !hasStart && !hasError;
  const doneCount = AGENT_ORDER.filter(
    (a) => agentInfo[a].status === "done",
  ).length;

  const idle = !selectedIncidentId;

  return (
    <section className="flex h-full flex-col">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-smoke-200">Agent activity</h2>
          {isConnecting && (
            <span className="flex items-center gap-1 text-[10px] text-smoke-500">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-smoke-500" />
              connecting
            </span>
          )}
          {isStreaming && (
            <span className="flex items-center gap-1 text-[10px] text-ember-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ember-400" />
              {doneCount}/{AGENT_ORDER.length}
            </span>
          )}
          {hasDone && (
            <span className="text-[10px] text-smoke-400">
              {doneCount}/{AGENT_ORDER.length} complete
            </span>
          )}
        </div>
        {(hasDone || hasError) && selectedIncidentId && (
          <button
            onClick={requestRestart}
            className="flex items-center gap-1 rounded px-2 py-0.5 text-[10px] text-smoke-400 hover:bg-smoke-700 hover:text-smoke-200"
          >
            <RefreshCw className="h-3 w-3" />
            Re-run
          </button>
        )}
      </header>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {idle && (
          <div className="px-4 py-6 text-xs text-smoke-500">
            Choose an incident from the left panel to start an analysis.
          </div>
        )}
        {hasError && (
          <div className="mx-4 mt-3 rounded bg-red-900/40 px-3 py-2 text-[11px] text-red-300 ring-1 ring-red-700/50">
            {String((events.find((e) => e.kind === "error")?.data as any) ?? "Stream error")}
          </div>
        )}
        {!idle && (
          <ol className="divide-y divide-smoke-700/50">
            {AGENT_ORDER.map((agent) => {
              const info = agentInfo[agent];
              return (
                <li key={agent} className="px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <StatusDot status={info.status} />
                    <span
                      className={`text-xs font-medium ${
                        info.status === "done"
                          ? "text-smoke-200"
                          : info.status === "running"
                            ? "text-ember-300"
                            : "text-smoke-500"
                      }`}
                    >
                      {AGENT_LABELS[agent]}
                    </span>
                    {info.confidence !== null && info.status === "done" && (
                      <span className="ml-auto text-[10px] text-smoke-500">
                        {Math.round(info.confidence * 100)}% conf
                      </span>
                    )}
                  </div>
                  {info.narrative && (
                    <p className="mt-1 pl-4 text-[11px] leading-snug text-smoke-400">
                      {stripAgentPrefix(info.narrative)}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </section>
  );
}

function StatusDot({ status }: { status: AgentStatus }) {
  if (status === "done") {
    return (
      <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-ember-900/60 ring-1 ring-ember-500/60">
        <span className="h-1.5 w-1.5 rounded-full bg-ember-400" />
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="h-3.5 w-3.5 animate-pulse rounded-full bg-ember-500/40 ring-1 ring-ember-500" />
    );
  }
  if (status === "error") {
    return <span className="h-3.5 w-3.5 rounded-full bg-red-600/40 ring-1 ring-red-500" />;
  }
  return <span className="h-3.5 w-3.5 rounded-full bg-smoke-700 ring-1 ring-smoke-600" />;
}

/** Strip "[agent_name] " prefix from stub narratives. */
function stripAgentPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
}
