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
  const popupRef = useRef<maplibregl.Popup | null>(null);
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

    // Create a shared reusable popup for hover tooltips
    popupRef.current = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      maxWidth: "240px",
      offset: 8,
    });

    return () => {
      popupRef.current?.remove();
      popupRef.current = null;
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

  // Render incidents as native WebGL circle layers so they zoom in sync with the map.
  // Previously these were HTML <button> Marker elements which lag behind the WebGL
  // rendering pipeline during zoom animations. Native layers are always perfectly synced.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded || !incidents) return;

    const geojson: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: incidents.map((inc) => ({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [inc.lon, inc.lat],
        },
        properties: {
          id: inc.id,
          name: inc.name,
          acres: inc.acres ?? 0,
          contained_pct:
            inc.contained_pct != null
              ? Math.round(inc.contained_pct * 100)
              : null,
          radius: sizeForAcres(inc.acres),
        },
      })),
    };

    // If the source already exists just update the data (incidents refreshed)
    const existing = map.getSource("incidents") as maplibregl.GeoJSONSource | undefined;
    if (existing) {
      existing.setData(geojson);
      return;
    }

    // First load: create source + layers
    map.addSource("incidents", { type: "geojson", data: geojson });

    // Soft glow ring behind each circle (wider, transparent)
    map.addLayer({
      id: "incidents-glow",
      type: "circle",
      source: "incidents",
      paint: {
        "circle-color": "rgba(249, 115, 22, 0.25)",
        "circle-radius": ["*", ["get", "radius"], 1.5],
        "circle-blur": 0.6,
        "circle-stroke-width": 0,
      },
    });

    // Main filled circle
    map.addLayer({
      id: "incidents-circle",
      type: "circle",
      source: "incidents",
      paint: {
        "circle-color": "rgba(249, 115, 22, 0.70)",
        "circle-radius": ["get", "radius"],
        "circle-stroke-color": "rgba(251, 146, 60, 0.85)",
        "circle-stroke-width": 1.5,
      },
    });

    // Pointer cursor on hover
    map.on("mouseenter", "incidents-circle", (e) => {
      map.getCanvas().style.cursor = "pointer";
      if (!e.features?.length || !popupRef.current) return;
      const props = e.features[0].properties as {
        name: string;
        acres: number;
        contained_pct: number | null;
      };
      const coords = (
        e.features[0].geometry as GeoJSON.Point
      ).coordinates as [number, number];
      popupRef.current
        .setLngLat(coords)
        .setHTML(
          `<div style="font-size:12px;line-height:1.5;color:#e2e8f0;background:#1e293b;padding:6px 8px;border-radius:6px;border:1px solid rgba(249,115,22,0.4)">
            <strong style="color:#fb923c">${props.name}</strong><br/>
            ${props.acres?.toLocaleString() ?? "?"} ac &middot; ${props.contained_pct ?? "?"}% contained
          </div>`,
        )
        .addTo(map);
    });

    map.on("mouseleave", "incidents-circle", () => {
      map.getCanvas().style.cursor = "";
      popupRef.current?.remove();
    });

    // Click to select
    map.on("click", "incidents-circle", (e) => {
      if (!e.features?.length) return;
      const id = e.features[0].properties.id as string;
      setSelectedIncident(id);
    });
  // setSelectedIncident is a stable Zustand selector; incidents + mapLoaded drive updates
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidents, mapLoaded]);

  // When setSelectedIncident callback reference changes (unlikely but defensive), keep click handler fresh
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    const handleClick = (
      e: maplibregl.MapMouseEvent & {
        features?: maplibregl.MapGeoJSONFeature[];
      },
    ) => {
      if (!e.features?.length) return;
      setSelectedIncident(e.features[0].properties.id as string);
    };

    map.on("click", "incidents-circle", handleClick);
    return () => {
      map.off("click", "incidents-circle", handleClick);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapLoaded, setSelectedIncident]);

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

      // Keep incident circles rendered above the perimeter layers
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");
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
