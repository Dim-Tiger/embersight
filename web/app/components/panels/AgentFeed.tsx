"use client";

import { AGENT_LABELS, AGENT_ORDER, type DialogueMessage, useStore } from "@/lib/store";
import { ArrowRight, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { MessageInput } from "./MessageInput";

export function AgentFeed() {
  const outputs = useStore((s) => s.agentOutputs);
  const statuses = useStore((s) => s.agentStatuses);
  const events = useStore((s) => s.agentEvents);
  const chat = useStore((s) => s.chat);
  const dialogue = useStore((s) => s.dialogue);
  const thinking = useStore((s) => s.thinking);
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

  // Tail events for the live "team in action" strip
  const tail = useMemo(() => {
    const out: { ts: number; label: string }[] = [];
    for (let i = events.length - 1; i >= 0 && out.length < 6; i--) {
      const e = events[i];
      const inner = e.data as Record<string, unknown> | undefined;
      const kind = (inner?.kind as string) ?? "";
      const name = (inner?.name as string) ?? "";
      if (kind === "on_chain_start" && AGENT_LABELS[name]) {
        out.push({ ts: e.ts, label: `▶ ${AGENT_LABELS[name]} started` });
      } else if (kind === "on_chain_end" && AGENT_LABELS[name]) {
        out.push({ ts: e.ts, label: `✓ ${AGENT_LABELS[name]} finished` });
      } else if (kind === "on_chat_model_stream") {
        out.push({ ts: e.ts, label: `· LLM token (${name || "model"})` });
      }
    }
    return out.reverse();
  }, [events]);

  const scrollRef = useRef<HTMLDivElement>(null);
  // Bytes of in-flight thinking are a decent proxy for "new content
  // appended" — re-run the near-bottom check whenever it changes.
  const thinkingBytes = useMemo(
    () => Object.values(thinking).reduce((a, b) => a + b.length, 0),
    [thinking],
  );
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Only auto-scroll if user is already near the bottom — don't yank them
    // away from a position they scrolled to manually.
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [chat.length, doneCount, dialogue.length, thinkingBytes]);

  return (
    <section className="flex h-full flex-col">
      {/* Header */}
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
            Re-run
          </button>
        )}
      </header>

      {/* Body */}
      <div className="flex min-h-0 flex-1 flex-col">
        {idle && (
          <div className="px-4 py-6 text-xs text-smoke-500">
            Choose an incident from the left panel to start an analysis.
          </div>
        )}
        {errorMessage && (
          <div className="mx-4 mt-3 rounded bg-red-900/40 px-3 py-2 text-[11px] text-red-300 ring-1 ring-red-700/50">
            {errorMessage}
          </div>
        )}
        {!idle && (
          <>
            {/* Single scroll container holds ladder + live stream + chat. */}
            <div
              ref={scrollRef}
              className="min-h-0 flex-1 overflow-y-auto"
            >
              {/* Pipeline ladder */}
              <ol className="divide-y divide-smoke-700/50">
                {AGENT_ORDER.map((agent) => {
                  const status = statuses[agent] ?? "pending";
                  const out = outputs[agent];
                  return (
                    <li
                      key={agent}
                      onClick={() =>
                        out && navigateForAgent(agent, setActiveTab)
                      }
                      className={`px-4 py-2 ${
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
                            {Math.round(out.confidence * 100)}% conf
                          </span>
                        )}
                      </div>
                      {out?.narrative && (
                        <p className="mt-1 pl-4 text-[11px] leading-snug text-smoke-400 line-clamp-3">
                          {stripPrefix(out.narrative)}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ol>

              {/* Live event tail */}
              <div className="border-t border-smoke-700 bg-smoke-900/60 px-4 py-2">
                <div className="mb-1 text-[9px] uppercase tracking-widest text-smoke-500">
                  Live stream
                </div>
                {tail.length === 0 ? (
                  <div className="text-[10px] italic text-smoke-600">
                    Waiting for events…
                  </div>
                ) : (
                  <ul className="space-y-0.5 font-mono text-[10px] text-smoke-400">
                    {tail.map((t, i) => (
                      <li key={i} className="truncate">
                        <span className="text-smoke-600">
                          {new Date(t.ts).toLocaleTimeString().slice(0, 8)}
                        </span>{" "}
                        {t.label}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Inter-agent dialogue + live thinking */}
              <DialogueTranscript dialogue={dialogue} thinking={thinking} />

              {/* User-facing chat trail (queries + final narratives) */}
              {chat.length > 0 && (
                <div className="space-y-2 border-t border-smoke-700 px-3 py-2">
                  {chat.map((m) => (
                    <ChatBubble
                      key={m.id}
                      role={m.role}
                      agentName={m.agentName}
                      text={m.text}
                    />
                  ))}
                </div>
              )}
            </div>

            <MessageInput />
          </>
        )}
      </div>
    </section>
  );
}

/**
 * Renders the orchestrator <-> subagent transcript: each request from the
 * orchestrator and the agent's response, with any live thinking tokens
 * streamed in between. The text is short-form by design — full narratives
 * still surface in the ChatBubble trail and in each tab's AgentCard.
 */
function DialogueTranscript({
  dialogue,
  thinking,
}: {
  dialogue: DialogueMessage[];
  thinking: Record<string, string>;
}) {
  // Find each agent's most recent request so we can attach in-flight
  // thinking tokens to it visually.
  const lastRequestByAgent = useMemo(() => {
    const out: Record<string, number> = {};
    dialogue.forEach((d, i) => {
      if (d.kind === "request") out[d.to] = i;
    });
    return out;
  }, [dialogue]);

  const hasAnyThinking = Object.keys(thinking).length > 0;

  if (dialogue.length === 0 && !hasAnyThinking) {
    return (
      <div className="border-t border-smoke-700 px-3 py-3 text-[11px] italic text-smoke-500">
        Inter-agent dialogue will appear here as the orchestrator dispatches
        each subagent.
      </div>
    );
  }

  return (
    <div className="space-y-1.5 border-t border-smoke-700 px-3 py-2">
      <div className="text-[9px] uppercase tracking-widest text-smoke-500">
        Orchestrator transcript
      </div>
      {dialogue.map((d, i) => {
        const showThinkingHere =
          d.kind === "request" &&
          lastRequestByAgent[d.to] === i &&
          thinking[d.to];
        return (
          <div key={d.id}>
            <DialogueLine d={d} />
            {showThinkingHere && (
              <ThinkingLine agent={d.to} text={thinking[d.to]} />
            )}
          </div>
        );
      })}
      {/* Thinking buffers for agents whose request didn't land in this list
          (rare, but happens when events arrive out of order). */}
      {Object.entries(thinking)
        .filter(([agent]) => lastRequestByAgent[agent] == null)
        .map(([agent, text]) => (
          <ThinkingLine key={agent} agent={agent} text={text} />
        ))}
    </div>
  );
}

function DialogueLine({ d }: { d: DialogueMessage }) {
  const fromLabel = labelFor(d.from);
  const toLabel = labelFor(d.to);
  const isOrchOut = d.from === "orchestrator";

  if (d.kind === "kickoff") {
    return (
      <div className="rounded bg-smoke-800/60 px-2 py-1 text-[11px] italic text-smoke-400">
        {d.text}
      </div>
    );
  }

  return (
    <div
      className={`rounded px-2 py-1.5 text-[11px] leading-snug ring-1 ${
        isOrchOut
          ? "bg-smoke-800/50 text-smoke-300 ring-smoke-700"
          : "bg-ember-900/20 text-ember-100 ring-ember-800/40"
      }`}
    >
      <div className="mb-0.5 flex items-center gap-1 text-[9px] font-semibold uppercase tracking-widest">
        <span className={isOrchOut ? "text-smoke-400" : "text-ember-300"}>
          {fromLabel}
        </span>
        <ArrowRight className="h-2.5 w-2.5 opacity-60" />
        <span className={isOrchOut ? "text-smoke-500" : "text-ember-400"}>
          {toLabel}
        </span>
        {d.kind === "response" && d.confidence != null && (
          <span className="ml-auto rounded bg-ember-900/40 px-1.5 py-0 text-[9px] font-semibold text-ember-200">
            {Math.round(d.confidence * 100)}% conf
          </span>
        )}
      </div>
      <div className="whitespace-pre-wrap">{d.text}</div>
      {d.confidenceDriver && (
        <div className="mt-0.5 text-[9px] italic text-smoke-500">
          {d.confidenceDriver}
        </div>
      )}
    </div>
  );
}

function ThinkingLine({ agent, text }: { agent: string; text: string }) {
  return (
    <div className="mt-0.5 ml-3 border-l border-smoke-700 px-2 py-1 text-[10px] leading-snug text-smoke-400">
      <div className="text-[8px] font-semibold uppercase tracking-widest text-smoke-500">
        {labelFor(agent)} · thinking
      </div>
      <div className="whitespace-pre-wrap italic">
        {text}
        <span className="ml-0.5 inline-block h-2 w-1 animate-pulse bg-smoke-500 align-middle" />
      </div>
    </div>
  );
}

function labelFor(slug: string): string {
  if (slug === "orchestrator") return "Orchestrator";
  if (slug === "team") return "Team";
  return AGENT_LABELS[slug] ?? slug;
}

function ChatBubble({
  role,
  agentName,
  text,
}: {
  role: "user" | "agent";
  agentName?: string;
  text: string;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded px-2.5 py-1.5 text-[11px] leading-snug ${
          isUser
            ? "bg-ember-600/80 text-white"
            : "bg-smoke-800 text-smoke-200 ring-1 ring-smoke-700"
        }`}
      >
        {!isUser && agentName && (
          <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-widest text-ember-300">
            {agentName}
          </div>
        )}
        <div className="whitespace-pre-wrap">{text}</div>
      </div>
    </div>
  );
}

function StatusDot({
  status,
}: {
  status: "pending" | "running" | "done" | "error";
}) {
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
    return (
      <span className="h-3.5 w-3.5 rounded-full bg-red-600/40 ring-1 ring-red-500" />
    );
  }
  return (
    <span className="h-3.5 w-3.5 rounded-full bg-smoke-700 ring-1 ring-smoke-600" />
  );
}

function stripPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
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
