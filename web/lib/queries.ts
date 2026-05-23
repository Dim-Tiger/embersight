"use client";

import { useQuery } from "@tanstack/react-query";

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

export async function postResume(
  threadId: string,
  decision: Record<string, unknown>,
): Promise<void> {
  const r = await fetch("/api/agent/resume", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, decision }),
  });
  if (!r.ok) throw new Error(`resume ${r.status}`);
  // We don't need to consume the SSE response here; the active stream
  // listener picks up subsequent events for the thread.
}
