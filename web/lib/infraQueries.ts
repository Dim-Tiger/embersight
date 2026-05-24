"use client";

import { useQueries } from "@tanstack/react-query";

import { INFRA_LAYERS, type InfraLayer } from "@/lib/infraLayers";

/** 1° lat ≈ 111 km. Used for the 25-km AOI around an incident. */
export function bboxAround(
  lat: number,
  lon: number,
  km: number,
): { west: number; south: number; east: number; north: number } {
  const dLat = km / 111.0;
  const dLon = km / (111.0 * Math.max(0.1, Math.cos((lat * Math.PI) / 180)));
  return {
    west: lon - dLon,
    south: lat - dLat,
    east: lon + dLon,
    north: lat + dLat,
  };
}

/** Quantize a coordinate to a 0.05-deg grid so adjacent fetches share a cache key. */
function snap(n: number): number {
  return Math.round(n / 0.05) * 0.05;
}

async function fetchPoi(
  layerId: string,
  bbox: { west: number; south: number; east: number; north: number },
): Promise<GeoJSON.FeatureCollection> {
  const bboxParam = `${bbox.west},${bbox.south},${bbox.east},${bbox.north}`;
  const r = await fetch(
    `/api/poi?source=${encodeURIComponent(layerId)}&bbox=${encodeURIComponent(bboxParam)}`,
  );
  if (!r.ok) return { type: "FeatureCollection", features: [] };
  return r.json();
}

/** Aggregated infra-layer fetch. Returns one result per INFRA_LAYERS entry,
 *  in the same order. Each query is gated by `visibility[layer.id]` so
 *  toggling a layer off cancels its fetch. */
export function useInfraLayers(
  lat: number | null,
  lon: number | null,
  visibility: Record<string, boolean>,
): Array<{ layer: InfraLayer; data: GeoJSON.FeatureCollection | undefined }> {
  const bbox = lat != null && lon != null ? bboxAround(lat, lon, 25) : null;
  const snappedKey =
    bbox != null
      ? `${snap(bbox.west)},${snap(bbox.south)},${snap(bbox.east)},${snap(bbox.north)}`
      : null;
  const results = useQueries({
    queries: INFRA_LAYERS.map((layer) => ({
      queryKey: ["poi", layer.id, snappedKey],
      queryFn: () => fetchPoi(layer.id, bbox!),
      enabled: !!visibility[layer.id] && bbox != null,
      staleTime: 60 * 60_000,
    })),
  });
  return INFRA_LAYERS.map((layer, i) => ({
    layer,
    data: results[i]?.data as GeoJSON.FeatureCollection | undefined,
  }));
}
