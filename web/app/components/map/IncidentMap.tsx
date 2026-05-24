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

type ConeImpact = {
  population_estimate?: number;
  residential_count?: number;
  structures_total?: number;
  hospitals_count?: number;
  hospitals_total_beds?: number;
  schools_count?: number;
  schools_total_enrollment?: number;
  transmission_segments?: number;
  transmission_max_kv?: number;
  critical_facilities_total?: number;
  error?: string;
};

type SpreadPayload = {
  cones?: Record<string, GeoJSON.Polygon | GeoJSON.MultiPolygon | null>;
  cone_impact?: ConeImpact | null;
  head_ros_chains_per_hr?: number | null;
  flame_length_ft?: number | null;
  burn_area_24h_km2_p25?: number | null;
};

export function IncidentMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const coneLabelRef = useRef<maplibregl.Marker | null>(null);
  const deckOverlayRef = useRef<MapboxOverlay | null>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [showWind, setShowWind] = useState(true);
  const [showEvac, setShowEvac] = useState(true);
  const [showFirms, setShowFirms] = useState(true);
  const [showCone, setShowCone] = useState(true);

  const { data: incidents } = useIncidents();
  const spread = useStore((s) => s.agentOutputs.spread_simulation);
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
  const { data: firms } = useFirms(1);

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

  // ---- Spread prediction cone (high-vis red, tornado-warning style) ----
  // Drawn from spread_simulation.payload.cones["24h"], labeled with
  // cone_impact (population + critical infra inside the cone).
  const cone24h = useMemo(() => {
    const payload = (spread?.payload ?? {}) as SpreadPayload;
    return payload.cones?.["24h"] ?? null;
  }, [spread]);

  const coneImpact = useMemo<ConeImpact | null>(() => {
    const payload = (spread?.payload ?? {}) as SpreadPayload;
    const impact = payload.cone_impact;
    if (!impact || impact.error) return null;
    return impact;
  }, [spread]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    try {
      if (map.getLayer("cone-fill")) map.removeLayer("cone-fill");
      if (map.getLayer("cone-outline")) map.removeLayer("cone-outline");
      if (map.getLayer("cone-outline-glow"))
        map.removeLayer("cone-outline-glow");
      if (map.getSource("cone")) map.removeSource("cone");
    } catch {
      /* mid-rerender */
    }
    if (coneLabelRef.current) {
      coneLabelRef.current.remove();
      coneLabelRef.current = null;
    }

    if (!showCone || !cone24h) return;

    // The spread agent's cone is a single ellipse with a rear vertex at the
    // incident point. We want the painted region to emanate from the ENTIRE
    // downwind perimeter outline — not from one point. Take the convex hull
    // of (perimeter vertices ∪ cone vertices). The hull wraps the fire's
    // current outline at the rear and tapers out to the cone's tip, which is
    // the tornado-warning-cone shape the user asked for.
    const hullCone = buildPerimeterCone(cone24h, perimeter ?? null);

    const features: GeoJSON.Feature[] = [
      { type: "Feature", geometry: hullCone, properties: { kind: "cone" } },
    ];
    if (perimeter?.features?.length) {
      for (const pf of perimeter.features) {
        if (
          pf.geometry?.type === "Polygon" ||
          pf.geometry?.type === "MultiPolygon"
        ) {
          features.push({
            type: "Feature",
            geometry: pf.geometry,
            properties: { kind: "perimeter" },
          });
        }
      }
    }
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features,
    };

    try {
      map.addSource("cone", { type: "geojson", data: fc });
      // Soft outer halo for contrast on dark basemap.
      map.addLayer({
        id: "cone-outline-glow",
        type: "line",
        source: "cone",
        paint: {
          "line-color": "#c026d3",
          "line-width": 7,
          "line-blur": 6,
          "line-opacity": 0.45,
        },
      });
      map.addLayer({
        id: "cone-fill",
        type: "fill",
        source: "cone",
        paint: {
          "fill-color": "#a855f7",
          "fill-opacity": 0.3,
          "fill-antialias": true,
        },
      });
      map.addLayer({
        id: "cone-outline",
        type: "line",
        source: "cone",
        // Only outline the projected cone — leave the inner perimeter
        // ring un-outlined so the two shapes read as one continuous body.
        filter: ["==", ["get", "kind"], "cone"],
        paint: {
          "line-color": "#d946ef",
          "line-width": 2.5,
          "line-opacity": 0.95,
        },
      });

      // Keep incident markers and perimeter on top of the cone.
      if (map.getLayer("perimeter-fill")) map.moveLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.moveLayer("perimeter-outline");
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");

      // Place an HTML label at the cone's centroid with impact data.
      const center = geometryCentroid(hullCone);
      if (center) {
        const el = document.createElement("div");
        el.className = "cone-impact-label";
        el.innerHTML = renderConeLabel(coneImpact);
        coneLabelRef.current = new maplibregl.Marker({
          element: el,
          anchor: "center",
        })
          .setLngLat(center)
          .addTo(map);
      }
    } catch (err) {
      console.warn("cone layer error:", err);
    }
  }, [cone24h, coneImpact, perimeter, showCone, mapLoaded]);

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
      const colorExpr = [
        "match",
        [
          "upcase",
          [
            "to-string",
            ["coalesce", ["get", "STATUS"], ["get", "status"], ""],
          ],
        ],
        ...Object.entries(EVAC_STATUS_COLORS).flatMap(([k, v]) => [k, v]),
        "#64748b",
      ] as unknown as maplibregl.ExpressionSpecification;

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

  // ---- VIIRS hotspots (NASA FIRMS) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    try {
      if (map.getLayer("firms-heat")) map.removeLayer("firms-heat");
      if (map.getLayer("firms-points")) map.removeLayer("firms-points");
      if (map.getSource("firms")) map.removeSource("firms");
    } catch {
      /* map may be mid-rerender */
    }

    if (!showFirms || !firms?.features?.length) return;

    try {
      map.addSource("firms", { type: "geojson", data: firms });

      // Heatmap at low zoom, individual hotspots at high zoom.
      map.addLayer({
        id: "firms-heat",
        type: "heatmap",
        source: "firms",
        maxzoom: 11,
        paint: {
          "heatmap-weight": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "frp"], 1],
            0, 0.1,
            50, 0.5,
            200, 1,
          ],
          "heatmap-intensity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            0, 1,
            11, 3,
          ],
          "heatmap-color": [
            "interpolate",
            ["linear"],
            ["heatmap-density"],
            0, "rgba(0,0,0,0)",
            0.2, "rgba(120, 60, 0, 0.4)",
            0.4, "rgba(220, 100, 0, 0.6)",
            0.7, "rgba(249, 115, 22, 0.8)",
            1.0, "rgba(252, 211, 77, 0.95)",
          ],
          "heatmap-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            0, 2,
            6, 12,
            11, 28,
          ],
          "heatmap-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            7, 0.85,
            11, 0.3,
          ],
        },
      });

      map.addLayer({
        id: "firms-points",
        type: "circle",
        source: "firms",
        minzoom: 8,
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "frp"], 1],
            0, 2.5,
            50, 5,
            200, 9,
          ],
          "circle-color": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "bright_ti4"], 320],
            300, "#fde68a",
            330, "#fb923c",
            360, "#dc2626",
            400, "#7f1d1d",
          ],
          "circle-stroke-color": "rgba(254, 215, 170, 0.6)",
          "circle-stroke-width": 0.5,
          "circle-opacity": [
            "interpolate",
            ["linear"],
            ["zoom"],
            8, 0.3,
            10, 0.85,
          ],
        },
      });

      const showFirmsPopup = (
        e: maplibregl.MapMouseEvent & {
          features?: maplibregl.MapGeoJSONFeature[];
        },
      ) => {
        if (!popupRef.current || !e.features?.length) return;
        const p = e.features[0].properties as Record<string, unknown>;
        const coords = (e.features[0].geometry as GeoJSON.Point)
          .coordinates as [number, number];
        const frp = p.frp != null ? Number(p.frp).toFixed(1) : "?";
        const bt = p.bright_ti4 != null ? Number(p.bright_ti4).toFixed(0) : "?";
        const conf = p.confidence ? String(p.confidence) : "—";
        const when = p.acq_datetime ? String(p.acq_datetime) : "—";
        popupRef.current
          .setLngLat(coords)
          .setHTML(
            `<div style="font-size:12px;line-height:1.5;color:#e2e8f0;background:#1e293b;padding:6px 8px;border-radius:6px;border:1px solid rgba(249,115,22,0.4)">
              <strong style="color:#fb923c">VIIRS hotspot</strong><br/>
              FRP ${frp} MW &middot; ${bt} K<br/>
              <span style="color:#94a3b8">conf ${conf} &middot; ${when}</span>
            </div>`,
          )
          .addTo(map);
        map.getCanvas().style.cursor = "pointer";
      };
      const hideFirmsPopup = () => {
        popupRef.current?.remove();
        map.getCanvas().style.cursor = "";
      };
      map.on("mousemove", "firms-points", showFirmsPopup);
      map.on("mouseleave", "firms-points", hideFirmsPopup);

      // Keep perimeter + incident markers above hotspots.
      if (map.getLayer("perimeter-fill")) map.moveLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.moveLayer("perimeter-outline");
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");
    } catch (err) {
      console.warn("firms layer error:", err);
    }
  }, [firms, showFirms, mapLoaded]);

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
      // Open-Meteo's wind_direction_10m is meteorological "FROM" — the
      // direction the wind originates from. maplibre-gl-wind's
      // generateWindTexture interprets `direction` as the heading the wind
      // travels TOWARD (u = speed·sin(dir), v = speed·cos(dir)). Pre-flip by
      // 180° here so the particle drift matches physical wind direction and
      // agrees with the spread-cone heading.
      const vectorsTo = wind.vectors.map((v) => ({
        ...v,
        direction: (v.direction + 180) % 360,
      }));
      const { canvas, uMin, uMax, vMin, vMax } = generateWindTexture(
        vectorsTo,
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
            // Dense-enough particle field with narrow lines + long
            // lifetime so each trail stretches ALONG the wind direction
            // (the path the particle walks). Wider `width` would have
            // stretched perpendicular to motion, which read as "fat
            // dots" rather than direction-indicating streaks.
            numParticles: 800,
            maxAge: 240,
            speedFactor: 55,
            width: 1.5,
            speedRange: [0, 25],
            // Contrail palette: faint tail → bright white core →
            // yellow → orange → red as wind speed climbs.
            colorRamp: [
              [0.0, [226, 232, 240, 180]], // slate-200 (faint tail)
              [0.2, [248, 250, 252, 230]], // near-white core
              [0.5, [253, 224, 71, 235]], // yellow-300
              [0.75, [249, 115, 22, 240]], // orange-500
              [1.0, [220, 38, 38, 250]], // red-600
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
        showFirms={showFirms}
        setShowFirms={setShowFirms}
        showCone={showCone}
        setShowCone={setShowCone}
        hasCone={!!cone24h}
        wind={wind}
        evacCount={evacFiltered?.features.length ?? 0}
        firmsCount={firms?.features?.length ?? 0}
      />
    </div>
  );
}

function geometryCentroid(
  geom: GeoJSON.Polygon | GeoJSON.MultiPolygon,
): [number, number] | null {
  // Lightweight centroid of the largest ring — good enough for label placement.
  const rings: GeoJSON.Position[][] =
    geom.type === "Polygon"
      ? [geom.coordinates[0]]
      : geom.coordinates.map((poly) => poly[0]);
  let best: GeoJSON.Position[] | null = null;
  let bestArea = -Infinity;
  for (const ring of rings) {
    if (!ring || ring.length < 3) continue;
    let area = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      area +=
        ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    }
    area = Math.abs(area) / 2;
    if (area > bestArea) {
      bestArea = area;
      best = ring;
    }
  }
  if (!best) return null;
  let x = 0;
  let y = 0;
  let n = 0;
  for (const [lon, lat] of best) {
    if (Number.isFinite(lon) && Number.isFinite(lat)) {
      x += lon;
      y += lat;
      n++;
    }
  }
  return n > 0 ? [x / n, y / n] : null;
}

function collectVertices(
  geom: GeoJSON.Polygon | GeoJSON.MultiPolygon,
  out: Array<[number, number]>,
): void {
  const rings: GeoJSON.Position[][] =
    geom.type === "Polygon" ? geom.coordinates : geom.coordinates.flat();
  for (const ring of rings) {
    for (const [lon, lat] of ring) {
      if (Number.isFinite(lon) && Number.isFinite(lat)) {
        out.push([lon as number, lat as number]);
      }
    }
  }
}

// Andrew's monotone-chain convex hull. Returns the hull vertices in
// counter-clockwise order, with the first vertex repeated at the end so the
// result is a valid GeoJSON linear ring.
function convexHull(points: Array<[number, number]>): Array<[number, number]> {
  if (points.length < 3) return points.slice();
  const pts = points
    .slice()
    .sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] - b[0]));
  const cross = (
    o: [number, number],
    a: [number, number],
    b: [number, number],
  ) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

  const lower: Array<[number, number]> = [];
  for (const p of pts) {
    while (
      lower.length >= 2 &&
      cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0
    ) {
      lower.pop();
    }
    lower.push(p);
  }
  const upper: Array<[number, number]> = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (
      upper.length >= 2 &&
      cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0
    ) {
      upper.pop();
    }
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  const hull = lower.concat(upper);
  if (hull.length > 0) hull.push(hull[0]);
  return hull;
}

function buildPerimeterCone(
  cone: GeoJSON.Polygon | GeoJSON.MultiPolygon,
  perimeter: GeoJSON.FeatureCollection | null,
): GeoJSON.Polygon {
  const verts: Array<[number, number]> = [];
  collectVertices(cone, verts);
  if (perimeter?.features?.length) {
    for (const f of perimeter.features) {
      const g = f.geometry;
      if (g?.type === "Polygon" || g?.type === "MultiPolygon") {
        collectVertices(g, verts);
      }
    }
  }
  const ring = convexHull(verts);
  return { type: "Polygon", coordinates: [ring] };
}

function fmtInt(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

function renderConeLabel(impact: ConeImpact | null): string {
  if (!impact) {
    return `
      <div style="font-family:ui-sans-serif,system-ui;font-size:11px;line-height:1.35;
        color:#f5e9ff;background:rgba(59,7,100,0.92);border:1.5px solid #d946ef;
        padding:6px 8px;border-radius:6px;box-shadow:0 2px 10px rgba(217,70,239,0.5);
        white-space:nowrap;letter-spacing:0.02em">
        <strong style="color:#f0abfc;text-transform:uppercase;font-size:10px">
          24h spread cone
        </strong>
        <div style="opacity:0.75">impact data unavailable</div>
      </div>
    `;
  }
  return `
    <div style="font-family:ui-sans-serif,system-ui;font-size:11px;line-height:1.4;
      color:#f5e9ff;background:rgba(59,7,100,0.92);border:1.5px solid #d946ef;
      padding:7px 9px;border-radius:7px;box-shadow:0 2px 12px rgba(217,70,239,0.55);
      min-width:170px;max-width:220px">
      <div style="font-weight:700;font-size:10px;letter-spacing:0.08em;
        text-transform:uppercase;color:#f0abfc;margin-bottom:4px">
        24h spread cone
      </div>
      <div style="font-weight:700;font-size:14px;color:#fff">
        ~${fmtInt(impact.population_estimate)} people
      </div>
      <div style="border-top:1px solid rgba(240,171,252,0.3);margin:5px 0 4px 0"></div>
      <div>🏠 ${fmtInt(impact.residential_count)} residences
        <span style="opacity:0.65">/ ${fmtInt(impact.structures_total)} total</span>
      </div>
      <div>🏥 ${fmtInt(impact.hospitals_count)} hospitals
        <span style="opacity:0.65">(${fmtInt(impact.hospitals_total_beds)} beds)</span>
      </div>
      <div>🏫 ${fmtInt(impact.schools_count)} schools
        <span style="opacity:0.65">(${fmtInt(impact.schools_total_enrollment)} students)</span>
      </div>
      <div>⚡ ${fmtInt(impact.transmission_segments)} TX lines
        ${
          impact.transmission_max_kv
            ? `<span style="opacity:0.65">max ${Math.round(impact.transmission_max_kv)} kV</span>`
            : ""
        }
      </div>
      <div>🚒 ${fmtInt(impact.critical_facilities_total)} critical facilities</div>
    </div>
  `;
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
  showFirms,
  setShowFirms,
  showCone,
  setShowCone,
  hasCone,
  wind,
  evacCount,
  firmsCount,
}: {
  hasPerimeter: boolean;
  showWind: boolean;
  setShowWind: (b: boolean) => void;
  showEvac: boolean;
  setShowEvac: (b: boolean) => void;
  showFirms: boolean;
  setShowFirms: (b: boolean) => void;
  showCone: boolean;
  setShowCone: (b: boolean) => void;
  hasCone: boolean;
  wind: WindGrid | undefined;
  evacCount: number;
  firmsCount: number;
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

      {hasCone && (
        <label className="mt-2 flex cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            checked={showCone}
            onChange={(e) => setShowCone(e.target.checked)}
            className="h-3 w-3 accent-fuchsia-500"
          />
          <span className="font-medium">24h spread cone</span>
        </label>
      )}
      {hasCone && showCone && (
        <div className="ml-5 mt-1 flex items-center gap-1.5 text-[10px] text-smoke-400">
          <span
            className="h-2 w-3 rounded-sm border"
            style={{
              backgroundColor: "rgba(168,85,247,0.35)",
              borderColor: "#d946ef",
            }}
          />
          <span>fire-prediction cone (purple)</span>
        </div>
      )}

      <label className="mt-2 flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showWind}
          onChange={(e) => setShowWind(e.target.checked)}
          className="h-3 w-3 accent-ember-500"
        />
        <span className="font-medium">Wind streams</span>
      </label>
      {showWind && (
        <div className="ml-5 mt-1 space-y-0.5 text-[10px] text-smoke-400">
          <div className="flex items-center gap-1.5">
            <svg
              width="44"
              height="8"
              viewBox="0 0 44 8"
              className="shrink-0"
              aria-hidden
            >
              <defs>
                <linearGradient id="wind-stream-grad" x1="0" x2="1" y1="0" y2="0">
                  <stop offset="0%" stopColor="#cbd5e1" stopOpacity="0" />
                  <stop offset="35%" stopColor="#f8fafc" stopOpacity="0.95" />
                  <stop offset="70%" stopColor="#fb923c" stopOpacity="1" />
                  <stop offset="100%" stopColor="#dc2626" stopOpacity="1" />
                </linearGradient>
              </defs>
              <path
                d="M1 5 Q 12 1, 22 4 T 43 3"
                stroke="url(#wind-stream-grad)"
                strokeWidth="1.2"
                strokeLinecap="round"
                fill="none"
              />
            </svg>
            <span>Streamlines — direction & speed</span>
          </div>
          {center && (
            <div>
              {center.speed.toFixed(1)} m/s ·{" "}
              {Math.round(center.direction)}°
              {wind?.gust_max_ms
                ? ` · gust ${wind.gust_max_ms.toFixed(0)} m/s`
                : ""}
            </div>
          )}
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

      <label className="mt-2 flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showFirms}
          onChange={(e) => setShowFirms(e.target.checked)}
          className="h-3 w-3 accent-ember-500"
        />
        <span className="font-medium">
          VIIRS hotspots ({firmsCount})
        </span>
      </label>
      {showFirms && (
        <div className="ml-5 mt-1 flex items-center gap-1.5 text-[10px] text-smoke-400">
          <span
            className="h-2 w-12 rounded-sm"
            style={{
              background:
                "linear-gradient(to right, #fde68a, #fb923c, #dc2626, #7f1d1d)",
            }}
          />
          <span>cooler → hotter (24h)</span>
        </div>
      )}

      <div className="mt-2 text-[10px] text-smoke-500">
        Sources: NIFC · CalOES · Open-Meteo · NASA FIRMS
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
