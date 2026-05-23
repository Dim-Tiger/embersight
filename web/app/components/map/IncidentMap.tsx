"use client";

import { useIncidents, usePerimeter, type Incident } from "@/lib/queries";
import { useStore } from "@/lib/store";
import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

const CARTO_DARK =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

export function IncidentMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const [mapLoaded, setMapLoaded] = useState(false);

  const { data: incidents } = useIncidents();
  const viewport = useStore((s) => s.mapViewport);
  const setSelectedIncident = useStore((s) => s.setSelectedIncident);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);

  const selectedIncident = incidents?.find((i) => i.id === selectedIncidentId);
  const irwinId = selectedIncidentId?.startsWith("wfigs:")
    ? selectedIncidentId.slice(6)
    : null;
  const { data: perimeter } = usePerimeter(
    selectedIncident?.lat ?? null,
    selectedIncident?.lon ?? null,
    irwinId,
  );

  // Init map
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: CARTO_DARK,
      center: [viewport.longitude, viewport.latitude],
      zoom: viewport.zoom,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => setMapLoaded(true));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      setMapLoaded(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fly to selected incident
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedIncident) return;
    map.flyTo({
      center: [selectedIncident.lon, selectedIncident.lat],
      zoom: 11,
      duration: 1400,
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIncidentId]);

  // Render incident markers
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !incidents) return;

    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];

    for (const inc of incidents) {
      const el = document.createElement("button");
      el.className =
        "rounded-full border border-ember-400/70 bg-ember-500/70 shadow-[0_0_10px_#f97316] " +
        "hover:scale-110 transition-transform cursor-pointer";
      const radius = sizeForAcres(inc.acres);
      el.style.width = `${radius}px`;
      el.style.height = `${radius}px`;
      el.title = `${inc.name} · ${inc.acres ?? "?"} ac · ${
        inc.contained_pct != null ? Math.round(inc.contained_pct * 100) : "?"
      }% contained`;

      el.addEventListener("click", () => {
        setSelectedIncident(inc.id);
      });

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([inc.lon, inc.lat])
        .addTo(map);
      markersRef.current.push(marker);
    }
  }, [incidents, setSelectedIncident]);

  // Render fire perimeter polygon
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    // Clean up previous perimeter layers/sources
    try {
      if (map.getLayer("perimeter-fill")) map.removeLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.removeLayer("perimeter-outline");
      if (map.getSource("perimeter")) map.removeSource("perimeter");
    } catch {
      // map may be mid-rerender; skip cleanup
    }

    if (!perimeter?.features?.length) return;

    try {
      map.addSource("perimeter", { type: "geojson", data: perimeter });
      map.addLayer({
        id: "perimeter-fill",
        type: "fill",
        source: "perimeter",
        paint: {
          "fill-color": "#f97316",
          "fill-opacity": 0.12,
        },
      });
      map.addLayer({
        id: "perimeter-outline",
        type: "line",
        source: "perimeter",
        paint: {
          "line-color": "#f97316",
          "line-width": 2,
          "line-opacity": 0.85,
          "line-dasharray": [2, 1],
        },
      });
    } catch (err) {
      console.warn("perimeter layer error:", err);
    }
  }, [perimeter, mapLoaded]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      <Legend hasPerimeter={!!perimeter?.features?.length} />
    </div>
  );
}

function sizeForAcres(acres: Incident["acres"]): number {
  if (!acres || acres <= 0) return 10;
  return Math.max(8, Math.min(36, Math.sqrt(acres) * 1.2));
}

function Legend({ hasPerimeter }: { hasPerimeter: boolean }) {
  return (
    <div className="absolute bottom-3 left-3 rounded-md bg-smoke-800/90 p-3 text-[11px] text-smoke-200">
      <div className="mb-1 font-semibold text-smoke-200">Incidents</div>
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-ember-500" />
        Active fire (size ∝ √acres)
      </div>
      {hasPerimeter && (
        <div className="mt-1 flex items-center gap-2">
          <span className="h-0.5 w-4 border-t-2 border-dashed border-ember-500" />
          Fire perimeter (WFIGS)
        </div>
      )}
      <div className="mt-2 text-smoke-400">Click marker or use sidebar.</div>
    </div>
  );
}
