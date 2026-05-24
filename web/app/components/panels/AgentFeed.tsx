"use client";

import { AGENT_LABELS, AGENT_ORDER, useStore } from "@/lib/store";
import { RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { MessageInput } from "./MessageInput";

export function AgentFeed() {
  const outputs = useStore((s) => s.agentOutputs);
  const statuses = useStore((s) => s.agentStatuses);
  const events = useStore((s) => s.agentEvents);
  const chat = useStore((s) => s.chat);
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
  }, [chat.length, doneCount]);

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

              {/* Chat trail */}
              <div className="space-y-2 border-t border-smoke-700 px-3 py-2">
                {chat.length === 0 ? (
                  <div className="px-1 py-3 text-[11px] italic text-smoke-500">
                    The AI Master IC will brief you here once the team
                    completes the initial run. After that, type a question
                    below — the IC will delegate to specialists as needed.
                  </div>
                ) : (
                  chat.map((m) => <ChatBubble key={m.id} message={m} />)
                )}
              </div>
            </div>

            <MessageInput />
          </>
        )}
      </div>
    </section>
  );
}

function ChatBubble({
  message,
}: {
  message: import("@/lib/store").ChatMessage;
}) {
  const { role, agentName, text, streaming, toolCalls } = message;
  const isUser = role === "user";
  const isSystem = role === "system";

  if (isSystem) {
    // System ticker line (briefing-mode subagent narrative)
    return (
      <div className="flex justify-start">
        <div className="max-w-[90%] rounded border border-smoke-800 bg-smoke-900/40 px-2.5 py-1 text-[10px] leading-snug text-smoke-400">
          <span className="font-semibold uppercase tracking-widest text-smoke-500">
            {agentName ?? "team"}
          </span>{" "}
          · {text}
        </div>
      </div>
    );
  }

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

        {/* Tool-call delegation pills — render above the IC's reply text */}
        {toolCalls && toolCalls.length > 0 && (
          <ul className="mb-1.5 space-y-1">
            {toolCalls.map((tc) => (
              <li
                key={tc.id}
                className="flex items-start gap-1.5 rounded border border-smoke-700 bg-smoke-900/60 px-1.5 py-1 text-[10px]"
              >
                <span
                  className={`mt-0.5 h-1.5 w-1.5 flex-shrink-0 rounded-full ${
                    tc.status === "done"
                      ? "bg-ember-400"
                      : "animate-pulse bg-ember-300"
                  }`}
                />
                <div className="min-w-0">
                  <div className="font-medium text-ember-200">
                    Consulting {tc.agentLabel}
                    {tc.args && (tc.args as any).must_refresh
                      ? " (refresh)"
                      : ""}
                    {tc.status === "running" ? "…" : ""}
                  </div>
                  {tc.summary && typeof tc.summary === "object" && (
                    <div className="text-smoke-400">
                      {(tc.summary as any).confidence != null
                        ? `conf ${Math.round(
                            ((tc.summary as any).confidence as number) * 100,
                          )}%`
                        : ""}
                      {(tc.summary as any).status === "no_output"
                        ? " · no cached output"
                        : ""}
                      {(tc.summary as any).status === "error"
                        ? ` · ${(tc.summary as any).error}`
                        : ""}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="whitespace-pre-wrap">
          {text}
          {streaming && (
            <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-ember-300 align-middle" />
          )}
        </div>
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
