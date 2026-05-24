"use client";

import {
  AGENT_LABELS,
  AGENT_ORDER,
  type AgentStatus,
  type ChatMessage,
  type ToolCall,
  useStore,
} from "@/lib/store";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Crown,
  Flame,
  Home,
  Loader2,
  Map as MapIcon,
  Mountain,
  Network,
  Radio,
  RefreshCw,
  Route as RouteIcon,
  Sparkles,
  Truck,
  Wind,
} from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { Markdown, stripMarkdown } from "@/app/components/Markdown";
import { MessageInput } from "./MessageInput";

type AgentAccent = {
  text: string;
  bg: string;
  ring: string;
  bar: string;
  glow: string;
};

type AgentMeta = {
  Icon: typeof Radio;
  accent: AgentAccent;
  blurb: string;
};

// Per-agent visual identity. Each specialist gets its own hue so the user can
// pick them out at a glance in the conversation feed — the IC reads in ember,
// the others fan out across the spectrum.
const AGENT_META: Record<string, AgentMeta> = {
  master_ic: {
    Icon: Crown,
    accent: {
      text: "text-ember-300",
      bg: "bg-ember-900/40",
      ring: "ring-ember-700/60",
      bar: "bg-ember-500",
      glow: "shadow-[0_0_20px_-2px_rgba(249,115,22,0.45)]",
    },
    blurb: "Synthesizes every voice into IC guidance.",
  },
  orchestrator: {
    Icon: Network,
    accent: {
      text: "text-violet-300",
      bg: "bg-violet-900/30",
      ring: "ring-violet-700/50",
      bar: "bg-violet-500",
      glow: "shadow-[0_0_20px_-2px_rgba(139,92,246,0.45)]",
    },
    blurb: "Coordinates the team and fans out work.",
  },
  weather_wind: {
    Icon: Wind,
    accent: {
      text: "text-sky-300",
      bg: "bg-sky-900/30",
      ring: "ring-sky-700/50",
      bar: "bg-sky-500",
      glow: "shadow-[0_0_20px_-2px_rgba(56,189,248,0.45)]",
    },
    blurb: "Wind, RH, fuel moisture, red-flag warnings.",
  },
  terrain_fuel: {
    Icon: Mountain,
    accent: {
      text: "text-amber-300",
      bg: "bg-amber-900/30",
      ring: "ring-amber-700/50",
      bar: "bg-amber-500",
      glow: "shadow-[0_0_20px_-2px_rgba(245,158,11,0.45)]",
    },
    blurb: "Slope, aspect, fuel models, fireshed.",
  },
  spread_simulation: {
    Icon: Flame,
    accent: {
      text: "text-red-300",
      bg: "bg-red-900/30",
      ring: "ring-red-700/50",
      bar: "bg-red-500",
      glow: "shadow-[0_0_20px_-2px_rgba(239,68,68,0.45)]",
    },
    blurb: "Projected fire spread under current weather.",
  },
  values_at_risk: {
    Icon: Home,
    accent: {
      text: "text-rose-300",
      bg: "bg-rose-900/30",
      ring: "ring-rose-700/50",
      bar: "bg-rose-500",
      glow: "shadow-[0_0_20px_-2px_rgba(244,63,94,0.45)]",
    },
    blurb: "Population, structures, critical infrastructure.",
  },
  routing_staging: {
    Icon: MapIcon,
    accent: {
      text: "text-blue-300",
      bg: "bg-blue-900/30",
      ring: "ring-blue-700/50",
      bar: "bg-blue-500",
      glow: "shadow-[0_0_20px_-2px_rgba(59,130,246,0.45)]",
    },
    blurb: "Roads, drop points, ingress and egress.",
  },
  resource_recommendation: {
    Icon: Truck,
    accent: {
      text: "text-emerald-300",
      bg: "bg-emerald-900/30",
      ring: "ring-emerald-700/50",
      bar: "bg-emerald-500",
      glow: "shadow-[0_0_20px_-2px_rgba(16,185,129,0.45)]",
    },
    blurb: "Crews, engines, air resources.",
  },
  evacuation_intelligence: {
    Icon: RouteIcon,
    accent: {
      text: "text-cyan-300",
      bg: "bg-cyan-900/30",
      ring: "ring-cyan-700/50",
      bar: "bg-cyan-500",
      glow: "shadow-[0_0_20px_-2px_rgba(34,211,238,0.45)]",
    },
    blurb: "Evac zones, capacity, shelters.",
  },
};

const DEFAULT_META: AgentMeta = {
  Icon: Network,
  accent: {
    text: "text-smoke-300",
    bg: "bg-smoke-800",
    ring: "ring-smoke-600",
    bar: "bg-smoke-500",
    glow: "",
  },
  blurb: "",
};

function metaFor(name: string): AgentMeta {
  return AGENT_META[name] ?? DEFAULT_META;
}

// Roster order: IC first (the voice you'll hear from), then the supporting
// cast. Different from AGENT_ORDER which prioritizes pipeline order — the
// roster prioritizes audibility for the human.
const ROSTER_ORDER = [
  "master_ic",
  "orchestrator",
  "weather_wind",
  "terrain_fuel",
  "spread_simulation",
  "values_at_risk",
  "routing_staging",
  "resource_recommendation",
  "evacuation_intelligence",
] as const;

export function BriefingTab() {
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const streaming = useStore((s) => s.streaming);
  const briefingComplete = useStore((s) => s.briefingComplete);
  const done = useStore((s) => s.done);
  const errorMessage = useStore((s) => s.errorMessage);
  const requestRestart = useStore((s) => s.requestRestart);
  const statuses = useStore((s) => s.agentStatuses);

  const doneCount = useMemo(
    () => AGENT_ORDER.filter((a) => statuses[a] === "done").length,
    [statuses],
  );
  const runningAgent = useMemo(
    () => AGENT_ORDER.find((a) => statuses[a] === "running") ?? null,
    [statuses],
  );

  return (
    <div className="flex h-full min-h-0 flex-col bg-smoke-900">
      <BriefingHeader
        streaming={streaming}
        done={done}
        briefingComplete={briefingComplete}
        doneCount={doneCount}
        totalAgents={AGENT_ORDER.length}
        runningAgent={runningAgent}
        onRestart={requestRestart}
        hasIncident={!!selectedIncidentId}
        errorMessage={errorMessage}
      />

      <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)] gap-px bg-smoke-700">
        <aside className="min-h-0 overflow-hidden bg-smoke-900">
          <AgentRoster />
        </aside>
        <section className="flex min-h-0 flex-col bg-smoke-900">
          <ConversationFeed />
          <MessageInput />
        </section>
      </div>
    </div>
  );
}

function BriefingHeader({
  streaming,
  done,
  briefingComplete,
  doneCount,
  totalAgents,
  runningAgent,
  onRestart,
  hasIncident,
  errorMessage,
}: {
  streaming: boolean;
  done: boolean;
  briefingComplete: boolean;
  doneCount: number;
  totalAgents: number;
  runningAgent: string | null;
  onRestart: () => void;
  hasIncident: boolean;
  errorMessage: string | null;
}) {
  let statusLabel: string;
  let statusTone: "live" | "ready" | "idle" | "error";
  if (errorMessage) {
    statusLabel = "error";
    statusTone = "error";
  } else if (streaming) {
    statusLabel = "live";
    statusTone = "live";
  } else if (briefingComplete || done) {
    statusLabel = "ready";
    statusTone = "ready";
  } else {
    statusLabel = "idle";
    statusTone = "idle";
  }

  return (
    <header className="relative overflow-hidden border-b border-smoke-700 bg-gradient-to-br from-smoke-800/80 via-smoke-900 to-smoke-900 px-6 py-3">
      {streaming && <HeaderShimmer />}
      <div className="relative flex items-center gap-4">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md bg-ember-900/50 ring-1 ring-ember-700/60">
          <Radio className="h-5 w-5 text-ember-300" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-3">
            <h1 className="text-base font-semibold text-smoke-100">
              Live Briefing
            </h1>
            <p className="text-[10px] uppercase tracking-widest text-smoke-500">
              talk to the master IC · watch the team work
            </p>
          </div>
          <p className="mt-0.5 text-[11px] text-smoke-400">
            {!hasIncident
              ? "Choose an active incident — the orchestrator will fan out to the specialists."
              : streaming && runningAgent
                ? `${AGENT_LABELS[runningAgent]} is on the radio — ${doneCount}/${totalAgents} agents reported`
                : streaming
                  ? `${doneCount}/${totalAgents} agents reported — synthesizing…`
                  : briefingComplete
                    ? `Briefing complete · ${doneCount}/${totalAgents} agents reported · ask the IC anything`
                    : `${doneCount}/${totalAgents} agents reported · standing by`}
          </p>
        </div>
        <StatusPill label={statusLabel} tone={statusTone} />
        {(done || errorMessage) && hasIncident && (
          <button
            onClick={onRestart}
            className="flex items-center gap-1 rounded border border-smoke-700 bg-smoke-800 px-2.5 py-1 text-[10px] font-medium text-smoke-300 transition-colors hover:border-ember-600/50 hover:bg-ember-900/30 hover:text-ember-200"
          >
            <RefreshCw className="h-3 w-3" />
            Re-brief
          </button>
        )}
      </div>
    </header>
  );
}

function StatusPill({
  label,
  tone,
}: {
  label: string;
  tone: "live" | "ready" | "idle" | "error";
}) {
  if (tone === "live") {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-ember-600/90 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-white">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white/70" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-white" />
        </span>
        {label}
      </span>
    );
  }
  if (tone === "ready") {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-emerald-700/80 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-white">
        <CheckCircle2 className="h-3 w-3" />
        {label}
      </span>
    );
  }
  if (tone === "error") {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-red-700/80 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-white">
        <AlertTriangle className="h-3 w-3" />
        {label}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-smoke-700 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-smoke-300">
      <CircleDashed className="h-3 w-3" />
      {label}
    </span>
  );
}

function HeaderShimmer() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute -inset-x-1/2 top-0 h-full w-1/3 -translate-x-full animate-[shimmer_4s_linear_infinite] bg-gradient-to-r from-transparent via-ember-500/[0.07] to-transparent" />
    </div>
  );
}

function AgentRoster() {
  const statuses = useStore((s) => s.agentStatuses);
  const outputs = useStore((s) => s.agentOutputs);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-smoke-300">
          The Team
        </h2>
        <span className="text-[10px] text-smoke-500">9 agents</span>
      </div>
      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2.5">
        {ROSTER_ORDER.map((name) => (
          <RosterCard
            key={name}
            name={name}
            status={statuses[name] ?? "pending"}
            narrative={outputs[name]?.narrative}
            confidence={outputs[name]?.confidence}
          />
        ))}
      </div>
    </div>
  );
}

function RosterCard({
  name,
  status,
  narrative,
  confidence,
}: {
  name: string;
  status: AgentStatus;
  narrative?: string;
  confidence?: number;
}) {
  const meta = metaFor(name);
  const { Icon, accent, blurb } = meta;
  const label = AGENT_LABELS[name] ?? name;

  const running = status === "running";
  const doneOk = status === "done";
  const errored = status === "error";

  const headline = narrative
    ? stripMarkdown(stripPrefix(narrative)).split(/[.;]\s/)[0].slice(0, 140)
    : null;

  const borderTone = running
    ? `${accent.ring} ${accent.glow}`
    : doneOk
      ? "ring-smoke-600/80"
      : errored
        ? "ring-red-800/60"
        : "ring-smoke-700";

  return (
    <div
      className={`relative overflow-hidden rounded-md bg-smoke-800/60 px-2.5 py-2 ring-1 transition-shadow ${borderTone}`}
    >
      {running && (
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute -inset-x-1/2 top-0 h-full w-1/3 -translate-x-full animate-[shimmer_3s_linear_infinite] bg-gradient-to-r from-transparent via-white/[0.04] to-transparent" />
        </div>
      )}
      <div className="relative flex items-start gap-2.5">
        <div
          className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded ${accent.bg}`}
        >
          <Icon className={`h-4 w-4 ${accent.text}`} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className={`truncate text-[12px] font-semibold ${
                running
                  ? accent.text
                  : doneOk
                    ? "text-smoke-100"
                    : "text-smoke-300"
              }`}
            >
              {label}
            </span>
            <span className="ml-auto flex-shrink-0">
              <RosterStatusBadge status={status} accent={accent} />
            </span>
          </div>
          <p
            className={`mt-0.5 line-clamp-2 text-[10px] leading-snug ${
              headline ? "text-smoke-300" : "italic text-smoke-500"
            }`}
          >
            {headline ?? blurb}
          </p>
          {doneOk && typeof confidence === "number" && (
            <ConfidenceBar value={confidence} barColor={accent.bar} />
          )}
        </div>
      </div>
    </div>
  );
}

function RosterStatusBadge({
  status,
  accent,
}: {
  status: AgentStatus;
  accent: AgentAccent;
}) {
  if (status === "running") {
    return (
      <span
        className={`inline-flex items-center gap-1 rounded-full ${accent.bg} px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${accent.text}`}
      >
        <Loader2 className="h-2.5 w-2.5 animate-spin" />
        live
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-900/40 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-emerald-200">
        <CheckCircle2 className="h-2.5 w-2.5" />
        in
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-900/40 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-red-200">
        <AlertTriangle className="h-2.5 w-2.5" />
        err
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-smoke-800 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-smoke-500 ring-1 ring-smoke-700">
      <CircleDashed className="h-2.5 w-2.5" />
      idle
    </span>
  );
}

function ConfidenceBar({
  value,
  barColor,
}: {
  value: number;
  barColor: string;
}) {
  const pct = Math.max(0, Math.min(1, value));
  return (
    <div className="mt-1.5 flex items-center gap-1.5">
      <div className="h-1 flex-1 overflow-hidden rounded-full bg-smoke-700">
        <div
          className={`h-full ${barColor} transition-[width] duration-500 ease-out`}
          style={{ width: `${Math.round(pct * 100)}%` }}
        />
      </div>
      <span className="font-mono text-[9px] text-smoke-400">
        {Math.round(pct * 100)}%
      </span>
    </div>
  );
}

function ConversationFeed() {
  const chat = useStore((s) => s.chat);
  const streaming = useStore((s) => s.streaming);
  const briefingComplete = useStore((s) => s.briefingComplete);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const errorMessage = useStore((s) => s.errorMessage);

  // Unlike the small ChatPanel, this full-page view keeps every entry
  // visible across mode changes — the briefing narratives ARE the value
  // here, and hiding them the moment the user asks a question feels like
  // the chat got wiped.
  const visible = chat;

  const scrollRef = useRef<HTMLDivElement>(null);
  const lastLengthRef = useRef(0);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Auto-scroll if the user is near the bottom OR if a new message just
    // arrived (so the first messages of a fresh briefing don't get hidden
    // above a non-scrolled viewport).
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    const grew = visible.length > lastLengthRef.current;
    if (nearBottom || grew) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
    lastLengthRef.current = visible.length;
  }, [visible.length, streaming]);

  const idle = !selectedIncidentId;
  const empty = !idle && visible.length === 0;

  return (
    <div
      ref={scrollRef}
      className="min-h-0 flex-1 overflow-y-auto px-6 py-4"
    >
      {idle && <EmptyHero variant="no-incident" />}
      {empty && (
        <EmptyHero
          variant={streaming ? "warming-up" : "ready-to-brief"}
          briefingComplete={briefingComplete}
        />
      )}
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {visible.map((m) => (
          <FeedItem key={m.id} message={m} />
        ))}
        {errorMessage && (
          <div className="flex items-start gap-2 rounded-md border border-red-700/60 bg-red-900/30 px-3 py-2 text-[11.5px] text-red-200">
            <AlertTriangle className="h-4 w-4 flex-shrink-0 text-red-300" />
            <div className="min-w-0">
              <div className="font-semibold">Stream error</div>
              <div className="text-red-300/90 break-words">{errorMessage}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyHero({
  variant,
  briefingComplete,
}: {
  variant: "no-incident" | "warming-up" | "ready-to-brief";
  briefingComplete?: boolean;
}) {
  if (variant === "no-incident") {
    return (
      <div className="mx-auto mt-12 flex max-w-md flex-col items-center gap-3 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-ember-900/40 ring-1 ring-ember-700/40">
          <Radio className="h-7 w-7 text-ember-400" />
        </div>
        <h3 className="text-sm font-semibold text-smoke-200">
          The radio is quiet.
        </h3>
        <p className="text-xs leading-relaxed text-smoke-400">
          Pick an active incident from the left to dispatch the team. The
          orchestrator will fan out to the nine specialists in parallel, then
          the Master IC will brief you here.
        </p>
      </div>
    );
  }
  if (variant === "warming-up") {
    return (
      <div className="mx-auto mt-8 flex max-w-md flex-col items-center gap-3 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-ember-900/40 ring-1 ring-ember-700/60">
          <Loader2 className="h-7 w-7 animate-spin text-ember-300" />
        </div>
        <h3 className="text-sm font-semibold text-smoke-200">
          Dispatching the team…
        </h3>
        <p className="text-xs leading-relaxed text-smoke-400">
          Specialists are gathering data. Their reports will surface here as
          they come in; the Master IC will speak once they've synthesized the
          picture.
        </p>
      </div>
    );
  }
  return (
    <div className="mx-auto mt-8 flex max-w-md flex-col items-center gap-3 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-900/30 ring-1 ring-emerald-700/40">
        <Sparkles className="h-7 w-7 text-emerald-300" />
      </div>
      <h3 className="text-sm font-semibold text-smoke-200">
        {briefingComplete ? "Briefing ready." : "Standing by."}
      </h3>
      <p className="text-xs leading-relaxed text-smoke-400">
        Ask the IC anything — wind shift, evacuation triggers, resource gaps —
        and the team will weigh in. Watch the roster on the left to see who's
        on the radio.
      </p>
    </div>
  );
}

function FeedItem({ message }: { message: ChatMessage }) {
  const { role } = message;
  if (role === "user") return <UserBubble message={message} />;
  if (role === "system") return <SystemNarrative message={message} />;
  return <AgentBubble message={message} />;
}

function UserBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[78%] rounded-2xl rounded-br-md bg-ember-600 px-3.5 py-2 text-[12.5px] leading-snug text-white shadow-sm">
        <div className="whitespace-pre-wrap">{message.text}</div>
        <div className="mt-1 text-right text-[9px] text-ember-100/70">
          {formatTime(message.ts)}
        </div>
      </div>
    </div>
  );
}

function AgentBubble({ message }: { message: ChatMessage }) {
  const { text, streaming, toolCalls, agentName, ts } = message;
  // Master IC owns these bubbles in chat mode — and the IC also closes the
  // briefing, so it's the right meta for the avatar.
  const meta = AGENT_META.master_ic;
  return (
    <div className="flex items-start gap-2.5">
      <Avatar meta={meta} />
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex items-baseline gap-2">
          <span className={`text-[11px] font-semibold ${meta.accent.text}`}>
            {agentName ?? "Master IC"}
          </span>
          <span className="text-[9px] text-smoke-500">{formatTime(ts)}</span>
          {streaming && (
            <span className="flex items-center gap-1 text-[9px] text-ember-300">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ember-400/70" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-ember-400" />
              </span>
              typing
            </span>
          )}
        </div>
        {toolCalls && toolCalls.length > 0 && (
          <ToolCallStack toolCalls={toolCalls} />
        )}
        <div
          className={`rounded-2xl rounded-tl-md bg-smoke-800 px-3.5 py-2 text-[12.5px] leading-relaxed text-smoke-100 ring-1 ring-smoke-700 ${
            text ? "" : "min-h-[2.5rem]"
          }`}
        >
          {text ? (
            <div>
              <Markdown>{text}</Markdown>
              {streaming && (
                <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-ember-300 align-middle" />
              )}
            </div>
          ) : streaming ? (
            <span className="text-[11px] italic text-smoke-500">
              composing…
            </span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ToolCallStack({ toolCalls }: { toolCalls: ToolCall[] }) {
  return (
    <div className="mb-1.5 space-y-1">
      {toolCalls.map((tc) => (
        <ToolCallRow key={tc.id} call={tc} />
      ))}
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCall }) {
  // The agent name embedded in the tool name (consult_weather_wind ->
  // weather_wind). Falls back to label if we can't recover the key.
  const agentKey = call.name.replace(/^consult_/, "");
  const meta = metaFor(agentKey);
  const { Icon, accent } = meta;
  const running = call.status === "running";
  const summary = call.summary as Record<string, unknown> | undefined;
  const conf =
    summary && typeof summary.confidence === "number"
      ? (summary.confidence as number)
      : null;
  const noOutput = summary?.status === "no_output";
  const err = summary?.status === "error";
  return (
    <div
      className={`flex items-center gap-2 rounded-md border border-smoke-700 bg-smoke-900/60 px-2 py-1 text-[10.5px] ${
        running ? accent.glow : ""
      }`}
    >
      <div
        className={`flex h-5 w-5 flex-shrink-0 items-center justify-center rounded ${accent.bg}`}
      >
        <Icon className={`h-3 w-3 ${accent.text}`} />
      </div>
      <span className="text-smoke-400">consulting</span>
      <span className={`font-semibold ${accent.text}`}>
        {call.agentLabel}
      </span>
      {running ? (
        <Loader2 className="ml-auto h-3 w-3 animate-spin text-ember-300" />
      ) : err ? (
        <span className="ml-auto text-red-300">
          {String(summary?.error ?? "error")}
        </span>
      ) : noOutput ? (
        <span className="ml-auto text-smoke-500">no cached output</span>
      ) : conf != null ? (
        <span className="ml-auto font-mono text-emerald-300">
          {Math.round(conf * 100)}% conf
        </span>
      ) : (
        <CheckCircle2 className="ml-auto h-3 w-3 text-emerald-400" />
      )}
    </div>
  );
}

function SystemNarrative({ message }: { message: ChatMessage }) {
  // Sub-agent narratives surface as "system" entries — render them as a
  // compact left-aligned bubble in the agent's color, distinct from the
  // larger IC bubble. This is the "the team is reporting in" view.
  const key = findAgentKey(message.agentName);
  const meta = metaFor(key ?? "");
  const { Icon, accent } = meta;
  return (
    <div className="flex items-start gap-2.5">
      <div
        className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full ${accent.bg} ring-1 ${accent.ring}`}
      >
        <Icon className={`h-3.5 w-3.5 ${accent.text}`} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex items-baseline gap-2">
          <span className={`text-[10.5px] font-semibold ${accent.text}`}>
            {message.agentName ?? "agent"}
          </span>
          <span className="text-[9px] uppercase tracking-widest text-smoke-600">
            reporting in
          </span>
          <span className="text-[9px] text-smoke-500">
            {formatTime(message.ts)}
          </span>
        </div>
        <div className="rounded-xl rounded-tl-md bg-smoke-800/60 px-3 py-1.5 text-[11.5px] leading-relaxed text-smoke-300 ring-1 ring-smoke-700/70">
          <Markdown>{message.text}</Markdown>
        </div>
      </div>
    </div>
  );
}

function Avatar({ meta }: { meta: AgentMeta }) {
  const { Icon, accent } = meta;
  return (
    <div
      className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full ${accent.bg} ring-1 ${accent.ring}`}
    >
      <Icon className={`h-4 w-4 ${accent.text}`} />
    </div>
  );
}

function findAgentKey(label?: string): string | null {
  if (!label) return null;
  for (const [key, value] of Object.entries(AGENT_LABELS)) {
    if (value === label) return key;
  }
  return null;
}

function stripPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
