"use client";

import { AGENT_LABELS, useStore } from "@/lib/store";
import { CircleCheck, CircleDashed, CloudOff, Loader2, OctagonAlert, PauseCircle } from "lucide-react";
import { INTERRUPT_AGENT } from "./triage";

/** Plain-language confidence band so "34%" is never left to interpretation. */
export function confidenceWord(c: number): "high" | "medium" | "low" {
  if (c >= 0.7) return "high";
  if (c >= 0.4) return "medium";
  return "low";
}

/**
 * One compact line per tab: which agents feed it, whether each has
 * reported, and how much to trust it. Replaces the tall activity banner
 * at the top of the tab (the banner's event log moves into a collapsed
 * section further down).
 */
export function AgentStatusRow({
  agents,
  gaps = {},
}: {
  agents: readonly string[];
  /** Per-agent data-gap reason. A high confidence score with a failed
   * source must not read as "high", so a gap overrides the band. */
  gaps?: Partial<Record<string, string | null>>;
}) {
  const statuses = useStore((s) => s.agentStatuses);
  const outputs = useStore((s) => s.agentOutputs);
  const done = useStore((s) => s.done);
  const streaming = useStore((s) => s.streaming);
  const pending = useStore((s) => s.pendingInterrupts);
  const runFinished = done && !streaming;
  const blocked = new Set(pending.map((p) => INTERRUPT_AGENT[p.interrupt.type]));

  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Agent status">
      {agents.map((a) => {
        const status = statuses[a] ?? "pending";
        const out = outputs[a];
        const label = AGENT_LABELS[a] ?? a;
        let Icon = CircleDashed;
        let text = "waiting";
        let cls = "border-smoke-600 text-smoke-300";
        if (status === "running") {
          Icon = Loader2;
          text = "working";
          cls = "border-ember-700 text-ember-200";
        } else if (status === "error") {
          Icon = OctagonAlert;
          text = "error";
          cls = "border-sev-critical-edge text-smoke-100";
        } else if (blocked.has(a) && status !== "done") {
          Icon = PauseCircle;
          text = "awaiting your approval";
          cls = "border-sev-warning-edge text-smoke-100";
        } else if (status === "done" && out) {
          const word = confidenceWord(out.confidence);
          const gap = gaps[a];
          Icon = word === "low" || gap ? CloudOff : CircleCheck;
          text = `${Math.round(out.confidence * 100)}% conf · ${gap ? "data gap" : word}`;
          cls =
            word === "low" || gap
              ? "border-sev-degraded-edge text-smoke-100"
              : "border-smoke-600 text-smoke-200";
        } else if (status === "done") {
          Icon = CircleCheck;
          text = "reported";
        } else if (runFinished) {
          Icon = CloudOff;
          text = "no report";
          cls = "border-sev-degraded-edge text-smoke-100";
        }
        return (
          <li
            key={a}
            title={gaps[a] ?? out?.confidence_driver ?? undefined}
            className={`inline-flex items-center gap-1.5 rounded-full border bg-smoke-800 px-2.5 py-1 text-[11px] ${cls}`}
          >
            <Icon
              className={`h-3.5 w-3.5 ${status === "running" ? "animate-spin" : ""}`}
              aria-hidden
            />
            <span className="font-semibold">{label}</span>
            <span className="text-smoke-300">{text}</span>
          </li>
        );
      })}
    </ul>
  );
}
