"use client";

import { useIncidents, type Incident } from "@/lib/queries";
import { useStore } from "@/lib/store";
import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import { useAgentStream } from "../panels/useAgentStream";

const CARTO_DARK =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

export function IncidentMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);

  const { data: incidents } = useIncidents();
  const viewport = useStore((s) => s.mapViewport);
  const setSelectedIncident = useStore((s) => s.setSelectedIncident);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const { start } = useAgentStream();

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
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Render incident markers as plain DOM (deck.gl wiring lands in pass 2)
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
        start(inc);
      });

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([inc.lon, inc.lat])
        .addTo(map);
      markersRef.current.push(marker);
    }
  }, [incidents, setSelectedIncident, start]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      {selectedIncidentId && (
        <div className="pointer-events-none absolute left-3 top-3 rounded-md bg-smoke-800/90 px-3 py-2 text-xs">
          Active incident:{" "}
          <span className="font-mono text-ember-200">{selectedIncidentId}</span>
        </div>
      )}
      <Legend />
    </div>
  );
}

function sizeForAcres(acres: Incident["acres"]): number {
  if (!acres || acres <= 0) return 10;
  return Math.max(8, Math.min(36, Math.sqrt(acres) * 1.2));
}

function Legend() {
  return (
    <div className="absolute bottom-3 left-3 rounded-md bg-smoke-800/90 p-3 text-[11px] text-smoke-200">
      <div className="mb-1 font-semibold text-smoke-200">Incidents</div>
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 rounded-full bg-ember-500" />
        Active fire (size ∝ √acres)
      </div>
      <div className="mt-2 text-smoke-400">Click to start an agent run.</div>
    </div>
  );
}
