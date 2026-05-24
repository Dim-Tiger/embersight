"use client";

import { AGENT_LABELS, AGENT_ORDER, useStore } from "@/lib/store";
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
      <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-smoke-200">
            Agent activity
          </h2>
          {isConnecting && (
            <span className="flex items-center gap-1 text-[10px] text-smoke-500">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-smoke-500" />
              {connectionStatus}
            </span>
          )}
          {streaming && doneCount > 0 && (
            <span className="flex items-center gap-1 text-[10px] text-ember-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ember-400" />
              {doneCount}/{AGENT_ORDER.length}
              {runningName ? ` · ${AGENT_LABELS[runningName]}` : ""}
            </span>
          )}
          {done && !streaming && (
            <span className="text-[10px] text-smoke-400">
              {doneCount}/{AGENT_ORDER.length} complete
            </span>
          )}
          {(streaming || done) && (
            <span className="font-mono text-[9px] text-smoke-600">
              {chunkCount}c · {frameCount}f
            </span>
          )}
        </div>
        {(done || errorMessage) && selectedIncidentId && (
          <button
            onClick={requestRestart}
            className="flex items-center gap-1 rounded px-2 py-0.5 text-[10px] text-smoke-400 hover:bg-smoke-700 hover:text-smoke-200"
          >
            <RefreshCw className="h-3 w-3" />
            Re-brief
          </button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {idle && (
          <div className="px-4 py-6 text-xs text-smoke-500">
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
              const status = statuses[agent] ?? "pending";
              const out = outputs[agent];
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
                            : "text-smoke-500"
                      }`}
                    >
                      {AGENT_LABELS[agent]}
                    </span>
                    {out?.confidence != null && status === "done" && (
                      <span className="ml-auto text-[10px] text-smoke-500">
                        {Math.round(out.confidence * 100)}%
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
  status: "pending" | "running" | "done" | "error";
}) {
  if (status === "done") {
    return (
      <span className="flex h-3 w-3 items-center justify-center rounded-full bg-ember-900/60 ring-1 ring-ember-500/60">
        <span className="h-1 w-1 rounded-full bg-ember-400" />
      </span>
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
