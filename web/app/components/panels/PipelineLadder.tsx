"use client";

import {
  AGENT_LABELS,
  AGENT_ORDER,
  useAwaitingAgents,
  useStore,
} from "@/lib/store";
import { RefreshCw } from "lucide-react";
import { useMemo } from "react";

export function PipelineLadder() {
  const outputs = useStore((s) => s.agentOutputs);
  const statuses = useStore((s) => s.agentStatuses);
  const streaming = useStore((s) => s.streaming);
  const done = useStore((s) => s.done);
  const errorMessage = useStore((s) => s.errorMessage);
  const connectionStatus = useStore((s) => s.connectionStatus);
  const chunkCount = useStore((s) => s.chunkCount);
  const frameCount = useStore((s) => s.frameCount);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const requestRestart = useStore((s) => s.requestRestart);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const awaiting = useAwaitingAgents();
  const pendingCount = useStore((s) => s.pendingInterrupts.length);

  const doneCount = useMemo(
    () => AGENT_ORDER.filter((a) => statuses[a] === "done").length,
    [statuses],
  );
  const runningName = useMemo(
    () => AGENT_ORDER.find((a) => statuses[a] === "running") ?? null,
    [statuses],
  );

  const idle = !selectedIncidentId;
  const isConnecting = !!selectedIncidentId && streaming && doneCount === 0;

  return (
    <section className="flex h-full min-h-0 flex-col">
      {/* Two rows so the status text never squeezes the title or the
          Re-brief button into wrapping at the 360 px ops column. The SSE
          chunk/frame counters are diagnostics, so they live in a tooltip. */}
      <header
        className="border-b border-smoke-700 bg-smoke-800/60 px-4 py-1.5"
        title={
          streaming || done
            ? `SSE diagnostics: ${chunkCount} chunks · ${frameCount} frames`
            : undefined
        }
      >
        <div className="flex items-center justify-between gap-2">
          <h2 className="whitespace-nowrap text-sm font-semibold text-smoke-200">
            Agent activity
          </h2>
          {(done || errorMessage) && selectedIncidentId && (
            <button
              onClick={requestRestart}
              className="flex items-center gap-1 whitespace-nowrap rounded px-2 py-0.5 text-[11px] text-smoke-300 hover:bg-smoke-700 hover:text-smoke-200"
              title="Run the whole briefing again for this incident"
            >
              <RefreshCw className="h-3 w-3" />
              Re-brief
            </button>
          )}
        </div>
        {isConnecting && (
          <div className="flex items-center gap-1 text-[10px] text-smoke-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-smoke-400" />
            connecting ({connectionStatus})
          </div>
        )}
        {streaming && doneCount > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-ember-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ember-400" />
            {doneCount}/{AGENT_ORDER.length} done
            {runningName ? ` · ${AGENT_LABELS[runningName]} working` : ""}
          </div>
        )}
        {done && !streaming && (
          <div className="text-[10px] text-smoke-400">
            {doneCount}/{AGENT_ORDER.length} complete
            {pendingCount > 0 && (
              <span className="text-amber-300"> · paused for IC decision</span>
            )}
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {idle && (
          <div className="px-4 py-6 text-xs text-smoke-400">
            Choose an incident to start the briefing.
          </div>
        )}
        {errorMessage && (
          <div className="mx-3 mt-2 rounded bg-red-900/40 px-2 py-1.5 text-[11px] text-red-300 ring-1 ring-red-700/50">
            {errorMessage}
          </div>
        )}
        {!idle && (
          <ol className="divide-y divide-smoke-700/50">
            {AGENT_ORDER.map((agent) => {
              const raw = statuses[agent] ?? "pending";
              const status =
                raw === "running" && awaiting.has(agent) ? "awaiting" : raw;
              const out = outputs[agent];
              // Agents that never started while the graph is paused run
              // only after the IC decides, so say "queued" instead of a
              // blank row that looks stuck.
              const queued =
                raw === "pending" && done && !streaming && pendingCount > 0;
              return (
                <li
                  key={agent}
                  onClick={() => out && navigateForAgent(agent, setActiveTab)}
                  className={`px-4 py-1.5 ${
                    out ? "cursor-pointer hover:bg-smoke-800/60" : ""
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <StatusDot status={status} />
                    <span
                      className={`text-xs font-medium ${
                        status === "done"
                          ? "text-smoke-200"
                          : status === "running"
                            ? "text-ember-300"
                            : status === "awaiting"
                              ? "text-amber-200"
                              : "text-smoke-400"
                      }`}
                    >
                      {AGENT_LABELS[agent]}
                    </span>
                    {status === "awaiting" && (
                      <span className="ml-auto text-[10px] font-medium text-amber-300">
                        awaiting your approval
                      </span>
                    )}
                    {queued && (
                      <span className="ml-auto text-[10px] text-smoke-400">
                        runs after IC decision
                      </span>
                    )}
                    {out?.confidence != null && status === "done" && (
                      <span
                        className="ml-auto text-[10px] text-smoke-400"
                        title="Agent self-reported confidence"
                      >
                        {Math.round(out.confidence * 100)}% conf
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </section>
  );
}

function StatusDot({
  status,
}: {
  status: "pending" | "running" | "done" | "error" | "awaiting";
}) {
  // Done is green everywhere else (nav dots, agent chips); orange here made
  // finished and running rows look alike.
  if (status === "done") {
    return (
      <span className="flex h-3 w-3 items-center justify-center rounded-full bg-emerald-900/60 ring-1 ring-emerald-500/70">
        <span className="h-1 w-1 rounded-full bg-emerald-400" />
      </span>
    );
  }
  if (status === "awaiting") {
    return (
      <span className="h-3 w-3 rounded-full bg-amber-400/30 ring-1 ring-amber-400" />
    );
  }
  if (status === "running") {
    return (
      <span className="h-3 w-3 animate-pulse rounded-full bg-ember-500/40 ring-1 ring-ember-500" />
    );
  }
  if (status === "error") {
    return <span className="h-3 w-3 rounded-full bg-red-600/40 ring-1 ring-red-500" />;
  }
  return <span className="h-3 w-3 rounded-full bg-smoke-700 ring-1 ring-smoke-600" />;
}

function navigateForAgent(
  agent: string,
  setTab: ReturnType<typeof useStore.getState>["setActiveTab"],
) {
  switch (agent) {
    case "weather_wind":
      setTab("Weather");
      return;
    case "values_at_risk":
    case "terrain_fuel":
      setTab("Threats");
      return;
    case "resource_recommendation":
    case "routing_staging":
      setTab("Resources");
      return;
    case "evacuation_intelligence":
      setTab("Evacuation");
      return;
    case "master_ic":
      setTab("IAP");
      return;
  }
}
