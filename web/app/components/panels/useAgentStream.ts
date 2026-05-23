"use client";

import { type Incident } from "@/lib/queries";
import { consumeAgentSse } from "@/lib/sse";
import { useStore } from "@/lib/store";
import { useCallback, useRef } from "react";

/**
 * SSE bridge that POSTs to /api/agent/stream and parses event-stream chunks
 * into the Zustand store via `consumeAgentSse`. The native EventSource API
 * only does GET, so we POST + read the body ourselves.
 */
export function useAgentStream() {
  const operationalPeriod = useStore((s) => s.operationalPeriod);
  const setSelectedThread = useStore((s) => s.setSelectedThread);
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(
    async (incident: Incident, opts?: { userQuery?: string }) => {
      abortRef.current?.abort();
      const store = useStore.getState();
      store.clearRun();
      store.setStreaming(true);

      const threadId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : String(Date.now());
      setSelectedThread(threadId);

      const userQuery = (opts?.userQuery ?? "").trim();
      if (userQuery) {
        store.appendChat({
          id: `u-${Date.now()}`,
          role: "user",
          text: userQuery,
          ts: Date.now(),
        });
      }

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      useStore.getState().setConnectionStatus("posting");

      try {
        const res = await fetch("/api/agent/stream", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            incident,
            operational_period: operationalPeriod,
            user_query: userQuery,
            thread_id: threadId,
          }),
          signal: ctrl.signal,
        });
        useStore.getState().setConnectionStatus("responded");

        if (!res.ok || !res.body) {
          const body = await res.text().catch(() => res.statusText);
          useStore.getState().setError(`stream ${res.status}: ${body.slice(0, 200)}`);
          useStore.getState().setStreaming(false);
          return;
        }

        await consumeAgentSse(res.body, threadId);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        useStore
          .getState()
          .setError(err instanceof Error ? err.message : String(err));
      } finally {
        useStore.getState().setStreaming(false);
      }
    },
    [operationalPeriod, setSelectedThread],
  );

  return { start };
}
