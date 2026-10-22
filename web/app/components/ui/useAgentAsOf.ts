"use client";

import { useStore } from "@/lib/store";
import { useMemo } from "react";

/**
 * Timestamp of the most recent report from any of `agents`, taken from
 * the agent event log ("on_chain_end" for the briefing, "tool_call_end"
 * when the IC re-consults an agent in chat). Null if none reported yet.
 *
 * AgentOutput itself carries no timestamp, so the event log is the only
 * honest source for an "as of" time.
 */
export function useAgentAsOf(agents: readonly string[]): number | null {
  const events = useStore((s) => s.agentEvents);
  return useMemo(() => {
    const set = new Set(agents);
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      const data = e.data as Record<string, unknown> | undefined;
      const kind = (data?.kind as string) ?? e.kind;
      const name = String(e.name ?? data?.name ?? "").replace(/^consult_/, "");
      if (!set.has(name)) continue;
      if (kind === "on_chain_end" || kind === "tool_call_end") return e.ts;
    }
    return null;
  }, [events, agents]);
}
