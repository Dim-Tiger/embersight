"use client";

import { type Incident } from "@/lib/queries";
import { type PendingInterrupt, useStore } from "@/lib/store";
import { useCallback, useRef } from "react";

/**
 * SSE bridge that POSTs to /api/agent/stream and parses event-stream chunks
 * into the Zustand store. The native EventSource API only does GET, so we
 * use fetch + ReadableStream and parse the protocol by hand.
 */
export function useAgentStream() {
  const appendEvent = useStore((s) => s.appendEvent);
  const clearEvents = useStore((s) => s.clearEvents);
  const upsertInterrupt = useStore((s) => s.upsertInterrupt);
  const setSelectedThread = useStore((s) => s.setSelectedThread);
  const operationalPeriod = useStore((s) => s.operationalPeriod);
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(
    async (incident: Incident) => {
      abortRef.current?.abort();
      clearEvents();

      const threadId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : String(Date.now());
      setSelectedThread(threadId);

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        const res = await fetch("/api/agent/stream", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            incident,
            operational_period: operationalPeriod,
            thread_id: threadId,
          }),
          signal: ctrl.signal,
        });

        if (!res.ok || !res.body) {
          appendEvent({
            ts: Date.now(),
            kind: "error",
            data: `stream ${res.status}`,
          });
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames separated by \n\n
          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            handleFrame(frame, {
              appendEvent,
              upsertInterrupt,
              threadId,
            });
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return; // intentional abort
        appendEvent({
          ts: Date.now(),
          kind: "error",
          data: err instanceof Error ? err.message : String(err),
        });
      }
    },
    [appendEvent, clearEvents, operationalPeriod, setSelectedThread, upsertInterrupt],
  );

  return { start };
}

function handleFrame(
  frame: string,
  ctx: {
    appendEvent: (e: { ts: number; kind: string; name?: string | null; data?: unknown }) => void;
    upsertInterrupt: (i: PendingInterrupt) => void;
    threadId: string;
  },
) {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return;
  let parsed: unknown = dataLines.join("\n");
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    /* keep as string */
  }

  if (event === "interrupt_pending" && parsed && typeof parsed === "object") {
    const p = parsed as { thread_id?: string; interrupt?: any };
    if (p.interrupt) {
      ctx.upsertInterrupt({
        thread_id: p.thread_id ?? ctx.threadId,
        interrupt: p.interrupt,
      });
    }
  }

  const evt =
    parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
  ctx.appendEvent({
    ts: Date.now(),
    kind: event,
    name: (evt.name as string) ?? null,
    data: parsed,
  });
}
