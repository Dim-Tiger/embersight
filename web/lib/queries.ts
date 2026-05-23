"use client";

import { useQuery } from "@tanstack/react-query";
import { consumeAgentSse } from "@/lib/sse";
import { useStore } from "@/lib/store";

export type Incident = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  acres?: number | null;
  contained_pct?: number | null;
  started_at?: string | null;
  source?: "calfire" | "wfigs" | "synthetic";
  raw?: Record<string, unknown>;
};

export function useIncidents() {
  return useQuery({
    queryKey: ["incidents"],
    queryFn: async (): Promise<Incident[]> => {
      const r = await fetch("/api/incidents");
      if (!r.ok) throw new Error(`incidents ${r.status}`);
      return r.json();
    },
    refetchInterval: 5 * 60_000,
  });
}

export function useEvacZones() {
  return useQuery({
    queryKey: ["evac"],
    queryFn: async () => {
      const r = await fetch("/api/evac");
      if (!r.ok) throw new Error(`evac ${r.status}`);
      return r.json();
    },
    refetchInterval: 5 * 60_000,
  });
}

export function useWeather(lat: number | null, lon: number | null) {
  return useQuery({
    queryKey: ["weather", lat, lon],
    enabled: lat != null && lon != null,
    queryFn: async () => {
      const r = await fetch(`/api/weather/${lat}/${lon}`);
      if (!r.ok) throw new Error(`weather ${r.status}`);
      return r.json();
    },
  });
}

export function usePerimeter(
  lat: number | null,
  lon: number | null,
  irwinId?: string | null,
) {
  return useQuery({
    queryKey: ["perimeter", lat, lon, irwinId],
    enabled: lat != null && lon != null,
    queryFn: async () => {
      const params = new URLSearchParams({
        lat: String(lat),
        lon: String(lon),
      });
      if (irwinId) params.set("irwinId", irwinId);
      const r = await fetch(`/api/perimeter?${params}`);
      if (!r.ok) return null;
      return r.json() as Promise<GeoJSON.FeatureCollection | null>;
    },
    refetchInterval: 5 * 60_000,
  });
}

/**
 * Resume a paused interrupt. We must actually consume the SSE body so the
 * FastAPI generator keeps running (the EventSourceResponse generator only
 * advances while its body is being read). Continuation events are routed
 * back into the same store through `consumeResumeStream`.
 */
export async function postResume(
  threadId: string,
  decision: Record<string, unknown>,
): Promise<void> {
  const r = await fetch("/api/agent/resume", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, decision }),
  });
  if (!r.ok || !r.body) throw new Error(`resume ${r.status}`);
  // Pump the resumed SSE through the same store handlers. We fire-and-forget
  // so the approval button returns immediately; continuation events keep
  // flowing into the dashboard as the graph advances post-interrupt.
  const store = useStore.getState();
  store.setStreaming(true);
  store.setDone(false);
  void consumeAgentSse(r.body, threadId).finally(() => {
    useStore.getState().setStreaming(false);
  });
}
