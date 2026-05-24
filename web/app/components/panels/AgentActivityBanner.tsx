"use client";

import {
  AGENT_LABELS,
  type AgentStatus,
  useStore,
} from "@/lib/store";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Loader2,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

type Props = {
  /** Ordered list of agent name keys to monitor. */
  agents: readonly string[];
  /** Hero title (e.g. "Weather Intelligence"). */
  title: string;
  /** Short subtitle / description shown under the title. */
  subtitle?: string;
  /** Icon shown on the left side of the banner. */
  icon?: ReactNode;
};

/**
 * Hero banner shown at the top of each subject-matter tab. Lets the user
 * actually watch the agents work: per-agent chips with live status, elapsed
 * timer while running, and a filtered ticker of recent events for the
 * relevant agents only.
 */
export function AgentActivityBanner({
  agents,
  title,
  subtitle,
  icon,
}: Props) {
  const statuses = useStore((s) => s.agentStatuses);
  const outputs = useStore((s) => s.agentOutputs);
  const events = useStore((s) => s.agentEvents);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const streaming = useStore((s) => s.streaming);

  const combined: AgentStatus = useMemo(() => {
    if (agents.some((a) => statuses[a] === "error")) return "error";
    if (agents.some((a) => statuses[a] === "running")) return "running";
    if (agents.length > 0 && agents.every((a) => statuses[a] === "done"))
      return "done";
    return "pending";
  }, [agents, statuses]);

  // Track the moment this banner's combined status entered "running" so we
  // can show an elapsed-time counter that resets between runs.
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const lastStatusRef = useRef<AgentStatus>(combined);
  useEffect(() => {
    if (combined === "running" && lastStatusRef.current !== "running") {
      setRunStartedAt(Date.now());
    } else if (combined !== "running" && lastStatusRef.current === "running") {
      // keep the finished elapsed value visible briefly; reset on next start
    }
    lastStatusRef.current = combined;
  }, [combined]);

  // Tick once per second while running so elapsed updates live.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (combined !== "running") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [combined]);

  const elapsed = runStartedAt
    ? Math.max(0, Math.round((now - runStartedAt) / 1000))
    : null;

  // Filter the event stream down to events that mention any of our agents,
  // keep the most recent N, and convert to display rows.
  const ticker = useMemo(() => {
    const set = new Set(agents);
    const rows: Array<{
      ts: number;
      label: string;
      tone: "start" | "end" | "stream" | "error" | "info";
      agent: string;
    }> = [];
    for (let i = events.length - 1; i >= 0 && rows.length < 8; i--) {
      const e = events[i];
      const data = e.data as Record<string, unknown> | undefined;
      const kind = (data?.kind as string) ?? e.kind;
      const name = (e.name as string) ?? (data?.name as string) ?? "";
      if (!set.has(name)) continue;
      if (kind === "on_chain_start") {
        rows.push({
          ts: e.ts,
          label: `${AGENT_LABELS[name] ?? name} started analysis`,
          tone: "start",
          agent: name,
        });
      } else if (kind === "on_chain_end") {
        rows.push({
          ts: e.ts,
          label: `${AGENT_LABELS[name] ?? name} finished`,
          tone: "end",
          agent: name,
        });
      } else if (kind === "on_tool_start") {
        const toolName = (data?.tool_name as string) ?? "tool";
        rows.push({
          ts: e.ts,
          label: `calling ${toolName}`,
          tone: "info",
          agent: name,
        });
      } else if (kind === "on_tool_end") {
        const toolName = (data?.tool_name as string) ?? "tool";
        rows.push({
          ts: e.ts,
          label: `${toolName} returned`,
          tone: "info",
          agent: name,
        });
      } else if (kind === "tool_call_start") {
        rows.push({
          ts: e.ts,
          label: `IC consulted ${AGENT_LABELS[name] ?? name}`,
          tone: "start",
          agent: name,
        });
      } else if (kind === "tool_call_end") {
        rows.push({
          ts: e.ts,
          label: `${AGENT_LABELS[name] ?? name} responded`,
          tone: "end",
          agent: name,
        });
      }
    }
    return rows.reverse();
  }, [events, agents]);

  // Pick a short "current focus" line: prefer the running agent's narrative
  // (if available) or the most recent ticker label.
  const currentFocus = useMemo(() => {
    const running = agents.find((a) => statuses[a] === "running");
    if (running) {
      const out = outputs[running];
      if (out?.narrative) return stripPrefix(out.narrative).slice(0, 140);
      return `${AGENT_LABELS[running] ?? running} is gathering data…`;
    }
    if (combined === "done") {
      const last = agents
        .map((a) => outputs[a])
        .filter(Boolean)
        .pop();
      if (last?.narrative) return stripPrefix(last.narrative).slice(0, 160);
      return "Analysis complete.";
    }
    return null;
  }, [agents, statuses, outputs, combined]);

  const tone = toneFor(combined);

  return (
    <section
      className={`relative overflow-hidden rounded-lg border ${tone.border} ${tone.bg}`}
    >
      {combined === "running" && <ShimmerOverlay />}

      <div className="relative flex items-start gap-3 px-4 py-3">
        <div
          className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md ${tone.iconBg}`}
        >
          <span className={tone.iconColor}>
            {icon ?? <Activity className="h-5 w-5" />}
          </span>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <h2 className="text-sm font-semibold text-smoke-100">{title}</h2>
            {subtitle && (
              <p className="text-[10px] uppercase tracking-widest text-smoke-500">
                {subtitle}
              </p>
            )}
            <div className="ml-auto flex items-center gap-2">
              <CombinedBadge status={combined} />
              {elapsed != null && combined === "running" && (
                <span className="font-mono text-[10px] text-ember-300">
                  {formatElapsed(elapsed)}
                </span>
              )}
              {combined === "done" && (
                <DoneSummaryBadge agents={agents} outputs={outputs} />
              )}
            </div>
          </div>

          {/* Per-agent chip row */}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {agents.map((a) => (
              <AgentChip
                key={a}
                name={a}
                status={statuses[a] ?? "pending"}
                confidence={outputs[a]?.confidence}
              />
            ))}
          </div>

          {/* Current focus line */}
          {currentFocus && (
            <p
              className={`mt-2 text-[12px] leading-snug ${
                combined === "running"
                  ? "text-smoke-200"
                  : combined === "done"
                    ? "text-smoke-300"
                    : "text-smoke-500"
              }`}
            >
              {combined === "running" && (
                <span className="mr-1 inline-block animate-pulse text-ember-300">
                  ●
                </span>
              )}
              {currentFocus}
            </p>
          )}

          {/* Live ticker */}
          {ticker.length > 0 && (
            <div className="mt-3 border-t border-smoke-700/60 pt-2">
              <div className="mb-1 flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-widest text-smoke-500">
                <Sparkles className="h-3 w-3" />
                Live agent activity
              </div>
              <ul className="space-y-0.5 font-mono text-[10px] text-smoke-400">
                {ticker.map((row, i) => (
                  <li
                    key={`${row.ts}-${i}`}
                    className="flex items-center gap-2 truncate"
                  >
                    <span
                      className={`flex-shrink-0 ${
                        row.tone === "start"
                          ? "text-ember-300"
                          : row.tone === "end"
                            ? "text-emerald-400"
                            : row.tone === "error"
                              ? "text-red-400"
                              : "text-smoke-500"
                      }`}
                    >
                      {row.tone === "start"
                        ? "▶"
                        : row.tone === "end"
                          ? "✓"
                          : row.tone === "error"
                            ? "✕"
                            : "·"}
                    </span>
                    <span className="text-smoke-600">
                      {new Date(row.ts).toLocaleTimeString().slice(0, 8)}
                    </span>
                    <span className="truncate text-smoke-300">{row.label}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Empty / idle state */}
          {!selectedIncidentId && (
            <p className="mt-2 text-[11px] italic text-smoke-500">
              Pick an active incident to dispatch the team.
            </p>
          )}
          {selectedIncidentId &&
            combined === "pending" &&
            !streaming && (
              <p className="mt-2 text-[11px] italic text-smoke-500">
                Waiting for the orchestrator to fan out to specialists…
              </p>
            )}
        </div>
      </div>
    </section>
  );
}

function AgentChip({
  name,
  status,
  confidence,
}: {
  name: string;
  status: AgentStatus;
  confidence?: number;
}) {
  const label = AGENT_LABELS[name] ?? name;
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-ember-900/40 px-2 py-0.5 text-[10px] font-medium text-ember-200 ring-1 ring-ember-700/60">
        <Loader2 className="h-2.5 w-2.5 animate-spin" />
        {label}
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-900/30 px-2 py-0.5 text-[10px] font-medium text-emerald-200 ring-1 ring-emerald-700/50">
        <CheckCircle2 className="h-2.5 w-2.5" />
        {label}
        {confidence != null && (
          <span className="text-emerald-300/80">
            · {Math.round(confidence * 100)}%
          </span>
        )}
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-900/40 px-2 py-0.5 text-[10px] font-medium text-red-200 ring-1 ring-red-700/60">
        <AlertTriangle className="h-2.5 w-2.5" />
        {label}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-smoke-800 px-2 py-0.5 text-[10px] font-medium text-smoke-500 ring-1 ring-smoke-700">
      <CircleDashed className="h-2.5 w-2.5" />
      {label}
    </span>
  );
}

function CombinedBadge({ status }: { status: AgentStatus }) {
  if (status === "running")
    return (
      <span className="flex items-center gap-1 rounded bg-ember-600/90 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white">
        <Loader2 className="h-3 w-3 animate-spin" />
        working
      </span>
    );
  if (status === "done")
    return (
      <span className="flex items-center gap-1 rounded bg-emerald-700/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white">
        <CheckCircle2 className="h-3 w-3" />
        ready
      </span>
    );
  if (status === "error")
    return (
      <span className="flex items-center gap-1 rounded bg-red-700/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white">
        <AlertTriangle className="h-3 w-3" />
        error
      </span>
    );
  return (
    <span className="flex items-center gap-1 rounded bg-smoke-700 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-smoke-300">
      <CircleDashed className="h-3 w-3" />
      standby
    </span>
  );
}

function DoneSummaryBadge({
  agents,
  outputs,
}: {
  agents: readonly string[];
  outputs: Record<string, { confidence?: number } | undefined>;
}) {
  const confs = agents
    .map((a) => outputs[a]?.confidence)
    .filter((c): c is number => typeof c === "number");
  if (!confs.length) return null;
  const avg = confs.reduce((s, c) => s + c, 0) / confs.length;
  return (
    <span className="rounded bg-smoke-800/60 px-2 py-0.5 text-[10px] font-medium text-emerald-200 ring-1 ring-emerald-800/60">
      avg {Math.round(avg * 100)}% conf
    </span>
  );
}

function ShimmerOverlay() {
  // A subtle sweep across the banner so the user feels the agent is doing work.
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute -inset-x-1/2 top-0 h-full w-1/3 -translate-x-full animate-[shimmer_3s_linear_infinite] bg-gradient-to-r from-transparent via-ember-500/[0.08] to-transparent" />
    </div>
  );
}

function toneFor(status: AgentStatus): {
  border: string;
  bg: string;
  iconBg: string;
  iconColor: string;
} {
  if (status === "running") {
    return {
      border: "border-ember-700/60",
      bg: "bg-gradient-to-br from-ember-900/30 via-smoke-800/40 to-smoke-800/60",
      iconBg: "bg-ember-900/60",
      iconColor: "text-ember-300",
    };
  }
  if (status === "done") {
    return {
      border: "border-emerald-700/40",
      bg: "bg-gradient-to-br from-emerald-900/20 via-smoke-800/40 to-smoke-800/60",
      iconBg: "bg-emerald-900/40",
      iconColor: "text-emerald-300",
    };
  }
  if (status === "error") {
    return {
      border: "border-red-700/50",
      bg: "bg-red-900/20",
      iconBg: "bg-red-900/60",
      iconColor: "text-red-300",
    };
  }
  return {
    border: "border-smoke-700",
    bg: "bg-smoke-800/40",
    iconBg: "bg-smoke-800",
    iconColor: "text-smoke-400",
  };
}

function formatElapsed(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function stripPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
}
