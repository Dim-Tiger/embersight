"use client";

import {
  useEvacZones,
  useFirms,
  useIncidents,
  usePerimeter,
  useWind,
  type Incident,
  type WindGrid,
} from "@/lib/queries";
import { useStore } from "@/lib/store";
import { MapboxOverlay } from "@deck.gl/mapbox";
import maplibregl from "maplibre-gl";
import { WindParticleLayer, generateWindTexture } from "maplibre-gl-wind";
import { useEffect, useMemo, useRef, useState } from "react";

const CARTO_DARK =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Evac zone status → color. Keys are normalized (uppercase, trimmed).
// Mirrors the Watch Duty / Genasys visual convention.
const EVAC_STATUS_COLORS: Record<string, string> = {
  "EVACUATION ORDER": "#dc2626", // red-600
  ORDER: "#dc2626",
  "EVACUATION WARNING": "#f59e0b", // amber-500
  WARNING: "#f59e0b",
  "SHELTER IN PLACE": "#facc15", // yellow-400
  SHELTER: "#facc15",
  ADVISORY: "#3b82f6", // blue-500
  "EVACUATION ADVISORY": "#3b82f6",
};

const ACTIVE_STATUSES = new Set(Object.keys(EVAC_STATUS_COLORS));

function normalizeStatus(raw: unknown): string {
  return String(raw ?? "")
    .trim()
    .toUpperCase();
}

export function IncidentMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const deckOverlayRef = useRef<MapboxOverlay | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [showWind, setShowWind] = useState(true);
  const [showEvac, setShowEvac] = useState(true);

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
  const { data: wind } = useWind(
    selectedIncident?.lat ?? null,
    selectedIncident?.lon ?? null,
  );
  const { data: evac } = useEvacZones();

  // Init map
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: CARTO_DARK,
      center: [viewport.longitude, viewport.latitude],
      zoom: viewport.zoom,
    });
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "top-right",
    );
    map.on("load", () => setMapLoaded(true));
    mapRef.current = map;

    popupRef.current = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      maxWidth: "260px",
      offset: 8,
    });

    return () => {
      popupRef.current?.remove();
      popupRef.current = null;
      if (deckOverlayRef.current) {
        map.removeControl(deckOverlayRef.current);
        deckOverlayRef.current = null;
      }
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

  // Render incidents as native WebGL circle layers
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

    const existing = map.getSource("incidents") as
      | maplibregl.GeoJSONSource
      | undefined;
    if (existing) {
      existing.setData(geojson);
      return;
    }

    map.addSource("incidents", { type: "geojson", data: geojson });

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

    map.on("mouseenter", "incidents-circle", (e) => {
      map.getCanvas().style.cursor = "pointer";
      if (!e.features?.length || !popupRef.current) return;
      const props = e.features[0].properties as {
        name: string;
        acres: number;
        contained_pct: number | null;
      };
      const coords = (e.features[0].geometry as GeoJSON.Point).coordinates as [
        number,
        number,
      ];
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

    map.on("click", "incidents-circle", (e) => {
      if (!e.features?.length) return;
      const id = e.features[0].properties.id as string;
      setSelectedIncident(id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidents, mapLoaded]);

  // Render fire perimeter polygon
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    try {
      if (map.getLayer("perimeter-fill")) map.removeLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline"))
        map.removeLayer("perimeter-outline");
      if (map.getSource("perimeter")) map.removeSource("perimeter");
    } catch {
      /* map may be mid-rerender */
    }

    if (!perimeter?.features?.length) return;

    try {
      map.addSource("perimeter", { type: "geojson", data: perimeter });
      map.addLayer({
        id: "perimeter-fill",
        type: "fill",
        source: "perimeter",
        paint: { "fill-color": "#f97316", "fill-opacity": 0.12 },
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
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");
    } catch (err) {
      console.warn("perimeter layer error:", err);
    }
  }, [perimeter, mapLoaded]);

  // ---- Evac zone polygons (Cal OES / Zonehaven aggregation) ----
  // Filter the statewide feed to active zones in the incident's vicinity so
  // we don't paint hundreds of polygons across California.
  const evacFiltered = useMemo(() => {
    if (!evac?.features?.length) return null;
    const features = evac.features.filter(
      (f: GeoJSON.Feature) =>
        ACTIVE_STATUSES.has(
          normalizeStatus(
            (f.properties as Record<string, unknown> | undefined)?.STATUS ??
              (f.properties as Record<string, unknown> | undefined)?.status,
          ),
        ),
    );
    return { type: "FeatureCollection", features } as GeoJSON.FeatureCollection;
  }, [evac]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    try {
      if (map.getLayer("evac-fill")) map.removeLayer("evac-fill");
      if (map.getLayer("evac-outline")) map.removeLayer("evac-outline");
      if (map.getSource("evac")) map.removeSource("evac");
    } catch {
      /* map may be mid-rerender */
    }

    if (!showEvac || !evacFiltered?.features.length) return;

    try {
      map.addSource("evac", { type: "geojson", data: evacFiltered });

      // Build a `match` expression over the normalized STATUS. MapLibre
      // doesn't have an upper() expression, so we drive coloring off the
      // raw STATUS attribute and list both casings if needed.
      const colorExpr: maplibregl.ExpressionSpecification = [
        "match",
        ["upcase", ["to-string", ["coalesce", ["get", "STATUS"], ["get", "status"], ""]]],
        ...Object.entries(EVAC_STATUS_COLORS).flatMap(([k, v]) => [k, v]),
        "#64748b",
      ];

      map.addLayer({
        id: "evac-fill",
        type: "fill",
        source: "evac",
        paint: {
          "fill-color": colorExpr,
          "fill-opacity": 0.28,
        },
      });
      map.addLayer({
        id: "evac-outline",
        type: "line",
        source: "evac",
        paint: {
          "line-color": colorExpr,
          "line-width": 1.5,
          "line-opacity": 0.9,
        },
      });

      // Hover popup with zone id + status
      const showZonePopup = (
        e: maplibregl.MapMouseEvent & {
          features?: maplibregl.MapGeoJSONFeature[];
        },
      ) => {
        if (!popupRef.current || !e.features?.length) return;
        const p = e.features[0].properties as Record<string, unknown>;
        const status = normalizeStatus(p.STATUS ?? p.status) || "—";
        const zone =
          p.ZONE_NAME ??
          p.zone_name ??
          p.ZONE_ID ??
          p.zone_id ??
          p.NAME ??
          p.name ??
          "Zone";
        const color = EVAC_STATUS_COLORS[status] ?? "#64748b";
        popupRef.current
          .setLngLat(e.lngLat)
          .setHTML(
            `<div style="font-size:12px;line-height:1.5;color:#e2e8f0;background:#1e293b;padding:6px 8px;border-radius:6px;border:1px solid ${color}66">
              <strong style="color:${color}">${escapeHtml(String(zone))}</strong><br/>
              <span style="color:${color}">${status}</span>
            </div>`,
          )
          .addTo(map);
        map.getCanvas().style.cursor = "pointer";
      };
      const hideZonePopup = () => {
        popupRef.current?.remove();
        map.getCanvas().style.cursor = "";
      };
      map.on("mousemove", "evac-fill", showZonePopup);
      map.on("mouseleave", "evac-fill", hideZonePopup);

      // Keep perimeter + incidents above evac zones.
      if (map.getLayer("perimeter-fill")) map.moveLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.moveLayer("perimeter-outline");
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");
    } catch (err) {
      console.warn("evac layer error:", err);
    }
  }, [evacFiltered, showEvac, mapLoaded]);

  // ---- Wind particle layer (deck.gl overlay) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    // Tear down the existing overlay so we can rebuild with fresh data.
    if (deckOverlayRef.current) {
      try {
        map.removeControl(deckOverlayRef.current);
      } catch {
        /* removeControl is idempotent-ish; ignore */
      }
      deckOverlayRef.current = null;
    }

    if (!showWind || !wind?.vectors?.length) return;

    try {
      const { canvas, uMin, uMax, vMin, vMax } = generateWindTexture(
        wind.vectors,
        {
          width: 128,
          height: 128,
          bounds: wind.bounds,
        },
      );
      const minV = Math.min(uMin, vMin);
      const maxV = Math.max(uMax, vMax);

      const overlay = new MapboxOverlay({
        interleaved: false,
        layers: [
          new WindParticleLayer({
            id: "embersight-wind",
            image: canvas.toDataURL(),
            bounds: wind.bounds,
            imageUnscale: [minV, maxV],
            numParticles: 4096,
            maxAge: 60,
            speedFactor: 35,
            width: 1.4,
            speedRange: [0, 25],
            colorRamp: [
              [0.0, [148, 163, 184, 200]], // slate-400
              [0.35, [251, 191, 36, 220]], // amber-400
              [0.7, [249, 115, 22, 235]], // orange-500
              [1.0, [220, 38, 38, 245]], // red-600
            ],
          }),
        ],
      });
      map.addControl(overlay);
      deckOverlayRef.current = overlay;
    } catch (err) {
      console.warn("wind layer error:", err);
    }
  }, [wind, showWind, mapLoaded]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      <Legend
        hasPerimeter={!!perimeter?.features?.length}
        showWind={showWind}
        setShowWind={setShowWind}
        showEvac={showEvac}
        setShowEvac={setShowEvac}
        wind={wind}
        evacCount={evacFiltered?.features.length ?? 0}
      />
    </div>
  );
}

function sizeForAcres(acres: Incident["acres"]): number {
  if (!acres || acres <= 0) return 10;
  return Math.max(8, Math.min(36, Math.sqrt(acres) * 1.2));
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => {
    switch (c) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      default:
        return "&#39;";
    }
  });
}

function Legend({
  hasPerimeter,
  showWind,
  setShowWind,
  showEvac,
  setShowEvac,
  wind,
  evacCount,
}: {
  hasPerimeter: boolean;
  showWind: boolean;
  setShowWind: (b: boolean) => void;
  showEvac: boolean;
  setShowEvac: (b: boolean) => void;
  wind: WindGrid | undefined;
  evacCount: number;
}) {
  const center = wind?.vectors.length
    ? wind.vectors[Math.floor(wind.vectors.length / 2)]
    : null;
  return (
    <div className="absolute bottom-3 left-3 max-w-[260px] rounded-md bg-smoke-800/90 p-3 text-[11px] text-smoke-200 shadow-lg backdrop-blur">
      <div className="mb-1 font-semibold text-smoke-200">Layers</div>

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

      <label className="mt-2 flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showWind}
          onChange={(e) => setShowWind(e.target.checked)}
          className="h-3 w-3 accent-ember-500"
        />
        <span className="font-medium">Wind particles</span>
      </label>
      {showWind && center && (
        <div className="ml-5 text-[10px] text-smoke-400">
          {center.speed.toFixed(1)} m/s ·{" "}
          {Math.round(center.direction)}°
          {wind?.gust_max_ms
            ? ` · gust ${wind.gust_max_ms.toFixed(0)} m/s`
            : ""}
        </div>
      )}

      <label className="mt-2 flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showEvac}
          onChange={(e) => setShowEvac(e.target.checked)}
          className="h-3 w-3 accent-ember-500"
        />
        <span className="font-medium">Evac zones ({evacCount})</span>
      </label>
      {showEvac && (
        <div className="ml-5 mt-1 space-y-0.5 text-[10px]">
          <ZoneSwatch color="#dc2626" label="Order" />
          <ZoneSwatch color="#f59e0b" label="Warning" />
          <ZoneSwatch color="#facc15" label="Shelter in place" />
          <ZoneSwatch color="#3b82f6" label="Advisory" />
        </div>
      )}

      <div className="mt-2 text-[10px] text-smoke-500">
        Sources: NIFC · CalOES · Open-Meteo
      </div>
    </div>
  );
}

function ZoneSwatch({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        className="h-2 w-3 rounded-sm border"
        style={{ backgroundColor: `${color}55`, borderColor: color }}
      />
      <span className="text-smoke-300">{label}</span>
    </div>
  );
}
