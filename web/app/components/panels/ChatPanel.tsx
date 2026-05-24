"use client";

import { type ChatMessage, useStore } from "@/lib/store";
import { useEffect, useMemo, useRef } from "react";
import { MessageInput } from "./MessageInput";

export function ChatPanel() {
  const chat = useStore((s) => s.chat);
  const streaming = useStore((s) => s.streaming);
  const briefingComplete = useStore((s) => s.briefingComplete);
  const currentMode = useStore((s) => s.currentMode);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);

  const visible = useMemo(
    () => chat.filter((m) => m.role !== "system" || currentMode === "briefing"),
    [chat, currentMode],
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [visible.length, streaming]);

  const idle = !selectedIncidentId;

  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <h2 className="text-sm font-semibold text-smoke-200">
          AI Master IC
        </h2>
        <span className="text-[10px] text-smoke-500">
          {briefingComplete ? "ready" : streaming ? "briefing…" : "idle"}
        </span>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-2"
      >
        {idle && (
          <div className="px-1 py-3 text-[11px] italic text-smoke-500">
            Pick an incident — the IC will brief you here once their team
            completes the initial run.
          </div>
        )}
        {!idle && visible.length === 0 && (
          <div className="px-1 py-3 text-[11px] italic text-smoke-500">
            Briefing in progress. The IC will speak once the team's data is in.
          </div>
        )}
        {visible.map((m) => (
          <ChatBubble key={m.id} message={m} />
        ))}
      </div>

      <MessageInput />
    </section>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const { role, agentName, text, streaming, toolCalls } = message;
  const isUser = role === "user";
  const isSystem = role === "system";

  if (isSystem) {
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
                    {tc.args && (tc.args as any).must_refresh ? " (refresh)" : ""}
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
