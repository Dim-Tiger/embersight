"use client";

import { type Incident } from "@/lib/queries";
import { consumeAgentSse } from "@/lib/sse";
import { useStore } from "@/lib/store";
import { useCallback, useRef } from "react";

/**
 * Two-mode SSE bridge:
 *
 * - `startBriefing(incident)` runs the initial full-fan-out briefing once per
 *   incident. Populates every tab from the seven specialists in parallel.
 *
 * - `sendMessage(incident, text)` runs a single conversational turn against
 *   the same checkpointed thread. Only the Master IC runs; the IC decides
 *   whether to call any specialist tools.
 */
export function useAgentStream() {
  const operationalPeriod = useStore((s) => s.operationalPeriod);
  const setSelectedThread = useStore((s) => s.setSelectedThread);
  const abortRef = useRef<AbortController | null>(null);

  const startBriefing = useCallback(
    async (incident: Incident) => {
      abortRef.current?.abort();
      const store = useStore.getState();
      store.clearRun();
      store.setStreaming(true);

      const threadId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : String(Date.now());
      setSelectedThread(threadId);

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      useStore.getState().setConnectionStatus("posting");

      try {
        const res = await fetch("/api/agent/stream", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            incident,
            mode: "briefing",
            operational_period: operationalPeriod,
            thread_id: threadId,
          }),
          signal: ctrl.signal,
        });
        useStore.getState().setConnectionStatus("responded");

        if (!res.ok || !res.body) {
          const body = await res.text().catch(() => res.statusText);
          useStore
            .getState()
            .setError(`stream ${res.status}: ${body.slice(0, 200)}`);
          useStore.getState().setStreaming(false);
          return;
        }

        await consumeAgentSse(res.body, threadId);
      } catch (err) {
        // Ignore intentional aborts and network-closure errors that browsers
        // may surface as TypeError instead of AbortError when the stream is
        // already being consumed (sse.ts already handled state for real errors).
        if (isAbortLike(err)) return;
        useStore
          .getState()
          .setError(err instanceof Error ? err.message : String(err));
      } finally {
        useStore.getState().setStreaming(false);
      }
    },
    [operationalPeriod, setSelectedThread],
  );

  const sendMessage = useCallback(
    async (incident: Incident, text: string) => {
      const message = text.trim();
      if (!message) return;
      const store = useStore.getState();
      const threadId = store.selectedThreadId;
      if (!threadId) {
        store.setError(
          "No active thread — pick an incident first to brief the IC.",
        );
        return;
      }

      // Record the user turn immediately.
      store.appendChat({
        id: `u-${Date.now()}`,
        role: "user",
        text: message,
        ts: Date.now(),
      });
      store.setStreaming(true);
      store.setDone(false);
      store.setError(null);
      useStore.getState().setConnectionStatus("posting");

      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        const res = await fetch("/api/agent/stream", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            incident,
            mode: "chat",
            message,
            operational_period: useStore.getState().operationalPeriod,
            thread_id: threadId,
          }),
          signal: ctrl.signal,
        });
        useStore.getState().setConnectionStatus("responded");

        if (!res.ok || !res.body) {
          const body = await res.text().catch(() => res.statusText);
          useStore
            .getState()
            .setError(`chat ${res.status}: ${body.slice(0, 200)}`);
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
    [],
  );

  return { startBriefing, sendMessage };
}

/** Mirror of sse.ts isAbortLike — kept local to avoid a shared-module import. */
function isAbortLike(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (
    err instanceof TypeError &&
    /network error|Failed to fetch|BodyStreamBuffer was aborted/i.test(
      err.message,
    )
  )
    return true;
  return false;
}
