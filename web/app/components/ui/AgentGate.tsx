"use client";

import { AGENT_LABELS, useStore } from "@/lib/store";
import { CloudOff, Loader2, PauseCircle, RefreshCw } from "lucide-react";
import { type ReactNode } from "react";
import { INTERRUPT_AGENT } from "./triage";

/**
 * Renders `children` once `agent` has produced output. Until then it shows
 * one honest line about why there is nothing yet: still working, waiting
 * on the IC's approval, or did not report at all (with a retry). This
 * replaces the old "Waiting for the agent to start" text, which stayed on
 * screen forever when an agent never ran.
 */
export function AgentGate({
  agent,
  fallbackHint,
  children,
}: {
  agent: string;
  /** What the IC can do instead while this data is missing. */
  fallbackHint?: string;
  children: ReactNode;
}) {
  const status = useStore((s) => s.agentStatuses[agent] ?? "pending");
  const output = useStore((s) => s.agentOutputs[agent]);
  const done = useStore((s) => s.done);
  const streaming = useStore((s) => s.streaming);
  const selected = useStore((s) => s.selectedIncidentId);
  const errorMessage = useStore((s) => s.errorMessage);
  const pending = useStore((s) => s.pendingInterrupts);
  const requestRestart = useStore((s) => s.requestRestart);
  const label = AGENT_LABELS[agent] ?? agent;

  if (output) return <>{children}</>;

  const blocked = pending.some((p) => INTERRUPT_AGENT[p.interrupt.type] === agent);
  const runFinished = (done && !streaming) || !!errorMessage;

  if (!selected) {
    return <Notice>Pick an active incident and the team runs automatically.</Notice>;
  }
  if (status === "running" || (!runFinished && !blocked)) {
    return (
      <Notice icon={<Loader2 className="h-4 w-4 animate-spin" aria-hidden />}>
        {status === "running" ? `${label} is working…` : `Waiting for ${label} to start…`}
      </Notice>
    );
  }
  if (blocked) {
    return (
      <Notice icon={<PauseCircle className="h-4 w-4" aria-hidden />} tone="warning">
        {label} is paused until you approve its DRAFT in the approval queue.
      </Notice>
    );
  }
  return (
    <Notice icon={<CloudOff className="h-4 w-4" aria-hidden />} tone="degraded">
      <span className="font-semibold">
        {status === "error" ? `${label} reported an error.` : `${label} did not report on this run.`}
      </span>{" "}
      {errorMessage ? `Stream error: ${errorMessage}. ` : ""}
      {fallbackHint}
      <button
        onClick={requestRestart}
        className="ml-2 inline-flex items-center gap-1 rounded border border-smoke-600 bg-smoke-700 px-2 py-0.5 text-[12px] font-semibold text-smoke-100 hover:border-ember-500"
      >
        <RefreshCw className="h-3 w-3" aria-hidden />
        Re-brief
      </button>
    </Notice>
  );
}

function Notice({
  children,
  icon,
  tone = "neutral",
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "warning" | "degraded";
}) {
  const edge =
    tone === "warning"
      ? "border-l-sev-warning-edge"
      : tone === "degraded"
        ? "border-l-sev-degraded-edge"
        : "border-l-smoke-600";
  return (
    <div
      className={`flex items-start gap-2 rounded-md border border-l-4 border-smoke-700 bg-smoke-900/70 px-3 py-2.5 text-[13px] leading-snug text-smoke-200 ${edge}`}
    >
      {icon && <span className="mt-0.5 shrink-0 text-smoke-300">{icon}</span>}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export { Notice as GateNotice };
