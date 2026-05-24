"use client";

import { useQuery } from "@tanstack/react-query";
import { consumeAgentSse } from "@/lib/sse";
import { useStore } from "@/lib/store";
import {
  buildSyntheticAlerts,
  buildSyntheticWindGrid,
  useTestMode,
} from "@/lib/testMode";

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
  const testEnabled = useTestMode((s) => s.enabled);
  const synthetic = useTestMode((s) => s.syntheticIncidents);

  const query = useQuery({
    queryKey: ["incidents"],
    queryFn: async (): Promise<Incident[]> => {
      const r = await fetch("/api/incidents");
      if (!r.ok) throw new Error(`incidents ${r.status}`);
      return r.json();
    },
    refetchInterval: 5 * 60_000,
    // In test mode we still let the underlying fetch run — the user may want
    // to compare synthetic against real. We just prepend synthetic on top.
  });

  if (!testEnabled || synthetic.length === 0) return query;

  const overlay: Incident[] = synthetic.map((s) => ({
    id: s.id,
    name: s.name,
    lat: s.lat,
    lon: s.lon,
    acres: s.acres,
    contained_pct: s.contained_pct,
    started_at: s.started_at,
    source: "synthetic" as const,
  }));
  const merged = [...overlay, ...(query.data ?? [])];
  // Return a shallow-cloned query object so consumers see the merged data
  // without losing isLoading/isError state.
  return { ...query, data: merged } as typeof query;
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
  const testEnabled = useTestMode((s) => s.enabled);
  const alertPreset = useTestMode((s) => s.alertPreset);

  const query = useQuery({
    queryKey: ["weather", lat, lon],
    enabled: lat != null && lon != null && !testEnabled,
    queryFn: async () => {
      const r = await fetch(`/api/weather/${lat}/${lon}`);
      if (!r.ok) throw new Error(`weather ${r.status}`);
      return r.json();
    },
  });

  if (testEnabled) {
    return {
      ...query,
      data: buildSyntheticAlerts(alertPreset),
      isLoading: false,
      isError: false,
      error: null,
    } as typeof query;
  }
  return query;
}

export type WindVector = {
  lat: number;
  lon: number;
  speed: number;
  direction: number;
};

export type WindGrid = {
  vectors: WindVector[];
  bounds: [number, number, number, number];
  center: { lat: number; lon: number };
  gust_max_ms: number | null;
  sampled_at: string;
};

export function useWind(lat: number | null, lon: number | null) {
  const testEnabled = useTestMode((s) => s.enabled);
  const wind = useTestMode((s) => s.wind);

  const query = useQuery({
    queryKey: ["wind", lat, lon],
    enabled: lat != null && lon != null && !testEnabled,
    queryFn: async (): Promise<WindGrid> => {
      const r = await fetch(`/api/wind/${lat}/${lon}`);
      if (!r.ok) throw new Error(`wind ${r.status}`);
      return r.json();
    },
    refetchInterval: 5 * 60_000,
  });

  if (testEnabled && lat != null && lon != null) {
    return {
      ...query,
      data: buildSyntheticWindGrid(lat, lon, wind) as WindGrid,
      isLoading: false,
      isError: false,
      error: null,
    } as typeof query;
  }
  return query;
}

export function useFirms(days = 1) {
  return useQuery({
    queryKey: ["firms", days],
    queryFn: async (): Promise<GeoJSON.FeatureCollection | null> => {
      const r = await fetch(`/api/firms?days=${days}`);
      if (!r.ok) return null;
      return r.json();
    },
    refetchInterval: 10 * 60_000,
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
