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
import {
  INFRA_GROUPS,
  INFRA_LAYERS,
  type InfraLayer,
} from "@/lib/infraLayers";
import { useInfraLayers } from "@/lib/infraQueries";
import { useStore } from "@/lib/store";
import { useTestMode } from "@/lib/testMode";
import { MapboxOverlay } from "@deck.gl/mapbox";
import maplibregl from "maplibre-gl";
import { WindParticleLayer, generateWindTexture } from "maplibre-gl-wind";
import { useEffect, useMemo, useRef, useState } from "react";

const CARTO_DARK =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// When a perimeter is loaded and the map is zoomed in past this level, hide
// the redundant orange circle for that fire and let the perimeter speak for
// itself.  Below this threshold the circle reappears automatically.
const PERIMETER_HIDE_CIRCLE_ZOOM = 9;

// Below this zoom the 66km wind grid covers only a tiny patch of the screen,
// so we widen particles and bump density so streams stay readable.
const WIND_LOW_ZOOM_THRESHOLD = 8;

// Esri World Imagery — free satellite raster tiles. Inlined as a MapLibre
// style so we can hot-swap basemaps without keeping a second style.json.
const SATELLITE_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    "esri-world-imagery": {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution:
        "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    },
  },
  layers: [
    {
      id: "esri-world-imagery",
      type: "raster",
      source: "esri-world-imagery",
      minzoom: 0,
      maxzoom: 19,
    },
  ],
};

type Basemap = "dark" | "satellite";

type RoutingPayload = {
  primary_routes?: Array<{
    path?: Array<[number, number]>;
    length_km?: number;
    est_drive_minutes?: number;
    avg_speed_kph?: number;
    bearing_deg?: number;
  }>;
  egress_routes?: Array<{
    path?: Array<[number, number]>;
    length_km?: number;
    est_drive_minutes?: number;
    bearing?: string;
    bearing_deg?: number;
    wind_relation?: "upwind" | "crosswind" | "downwind" | "unknown";
    destination?: {
      name?: string;
      rally_type?: string;
      source?: string;
      capacity?: number | null;
      loc?: [number, number];
      score?: number;
    };
  }>;
  candidates?: Array<{
    name?: string;
    loc?: [number, number];
    score?: number;
    dist_incident_km?: number;
    nearest_water_km?: number;
    score_components?: Record<string, number>;
    score_weights?: Record<string, number>;
    score_raw?: Record<string, number | null>;
  }>;
  wind?: {
    from_deg?: number | null;
    speed_mph?: number | null;
    source?: string | null;
  };
  rally_points?: Array<{
    name?: string;
    loc?: [number, number];
    rally_type?: string;
    source?: string;
    capacity?: number | null;
    score?: number;
    wind_relation?: "upwind" | "crosswind" | "downwind" | "unknown";
  }>;
  egress_strategy?: "rally_points" | "bearings_fallback";
};

// Evac zone status → color. Keys are normalized (uppercase, trimmed).
// Mirrors the Watch Duty / Genasys visual convention.
const EVAC_STATUS_COLORS: Record<string, string> = {
  "EVACUATION ORDER": "#dc2626", // red-600
  ORDER: "#dc2626",
  "EVACUATION WARNING": "#facc15", // yellow-400
  WARNING: "#facc15",
  "SHELTER IN PLACE": "#a855f7", // purple-500
  SHELTER: "#a855f7",
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
  swept_cone_24h?: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
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
  // Tracks whether the most recent zoom delta came from the user (true) or
  // from a programmatic camera animation like flyTo (false). Read by the
  // "dismiss perimeter on zoom-out" effect so a flyTo dip doesn't count.
  const lastZoomFromUserRef = useRef(false);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [showWind, setShowWind] = useState(true);
  const [showEvac, setShowEvac] = useState(true);
  const [showFirms, setShowFirms] = useState(true);
  const [showRoutes, setShowRoutes] = useState(true);
  const [basemap, setBasemap] = useState<Basemap>("dark");
  const [showCone, setShowCone] = useState(true);
  const [legendCollapsed, setLegendCollapsed] = useState(false);
  const [windLowZoom, setWindLowZoom] = useState(
    () => useStore.getState().mapViewport.zoom < WIND_LOW_ZOOM_THRESHOLD,
  );

  // Store selectors — placed before the derived state that depends on them.
  const { data: incidents } = useIncidents();
  const spread = useStore((s) => s.agentOutputs.spread_simulation);
  const viewport = useStore((s) => s.mapViewport);
  const setSelectedIncident = useStore((s) => s.setSelectedIncident);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);

  // True while the map zoom is above PERIMETER_HIDE_CIRCLE_ZOOM.  We only
  // flip this boolean (not store the raw zoom) so we avoid a re-render on
  // every incremental scroll step.
  const [abovePerimeterZoom, setAbovePerimeterZoom] = useState(
    () => viewport.zoom >= PERIMETER_HIDE_CIRCLE_ZOOM,
  );
  // Tracks whether the perimeter "session" is active for the currently
  // selected fire.  Set to true when a fire is selected; cleared to false the
  // instant the user zooms out below the threshold.  Crucially it does NOT
  // flip back to true when the user zooms in again — only an explicit
  // re-selection does that.  This gives us the three-phase behaviour:
  //   select → zoom in  → perimeter on  / circle off
  //            zoom out → perimeter off / circle on   (dismissed)
  //            zoom in  → perimeter still off          (stays dismissed)
  //   re-select          → perimeter on  / circle off  (fresh session)
  const [perimeterEnabled, setPerimeterEnabled] = useState(false);

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
  const routingOutput = useStore(
    (s) => s.agentOutputs.routing_staging,
  ) as { payload?: RoutingPayload } | undefined;
  const pendingInterrupts = useStore((s) => s.pendingInterrupts);
  const acceptedEvacZones = useStore((s) => s.acceptedEvacZones);

  // Pull the polygons + status out of pending evac_zone_change interrupts so
  // we can paint a pulsing suggestion overlay on the map. Each suggestion has
  // a stable zone_id so the layer can find/update it between renders.
  const suggestedEvacZones = useMemo(() => {
    return pendingInterrupts
      .filter((p) => p.interrupt.type === "evac_zone_change")
      .map((p) => {
        const payload = (p.interrupt.payload ?? {}) as Record<string, unknown>;
        const status = String(payload.proposed_status ?? "").toUpperCase();
        if (status !== "WARNING" && status !== "ORDER") return null;
        const geom = payload.polygon_geojson as
          | GeoJSON.Polygon
          | GeoJSON.MultiPolygon
          | undefined;
        if (!geom) return null;
        return {
          zone_id: String(payload.zone_id ?? p.interrupt.id ?? Math.random()),
          name: String(payload.name ?? "Suggested zone"),
          status: status as "WARNING" | "ORDER",
          geom,
        };
      })
      .filter((x): x is NonNullable<typeof x> => x !== null);
  }, [pendingInterrupts]);

  // Critical-infrastructure overlays. Each entry's data is fetched only
  // when its toggle in infraVisibility is on; toggles persist across
  // basemap swaps and incident switches via the Zustand store.
  const infraVisibility = useStore((s) => s.infraVisibility);
  const toggleInfra = useStore((s) => s.toggleInfra);
  const infraResults = useInfraLayers(
    selectedIncident?.lat ?? null,
    selectedIncident?.lon ?? null,
    infraVisibility,
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
    map.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "top-right",
    );
    map.on("load", () => setMapLoaded(true));
    // Flip the boolean state only when crossing the perimeter-visibility
    // threshold so we don't trigger re-renders on every zoom tick.
    // Also remember whether the most recent zoom transition was driven by
    // the user (wheel, pinch, double-click) or by a programmatic camera
    // animation (flyTo, easeTo). MapLibre's parabolic flyTo curve briefly
    // dips zoom below the start position before climbing — without this
    // discrimination, switching between two fires at zoom 11 momentarily
    // crosses the perimeter threshold and trips the "user zoomed out"
    // dismiss-perimeter effect, hiding the perimeter on the new fire.
    map.on("zoom", (e) => {
      const z = map.getZoom();
      lastZoomFromUserRef.current = !!(
        e as unknown as { originalEvent?: unknown }
      ).originalEvent;
      setAbovePerimeterZoom((prev) => {
        const next = z >= PERIMETER_HIDE_CIRCLE_ZOOM;
        return prev === next ? prev : next;
      });
      setWindLowZoom((prev) => {
        const next = z < WIND_LOW_ZOOM_THRESHOLD;
        return prev === next ? prev : next;
      });
    });
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

  // Test-mode placement: when the dev panel toggles "Click map to place fire",
  // a single click on empty map drops a synthetic ignition at the click point
  // and selects it. We read the latest store values via getState() inside the
  // handler so the listener doesn't need to be re-attached every toggle.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;
    const handler = (e: maplibregl.MapMouseEvent) => {
      const t = useTestMode.getState();
      if (!t.placementMode) return;
      // Skip clicks landing on the incident layer — that path is owned by the
      // incident-circle handler.
      const hit = map.queryRenderedFeatures(e.point, {
        layers: ["incidents-circle"].filter((id) => map.getLayer(id)),
      });
      if (hit.length > 0) return;
      if (!t.enabled) t.setEnabled(true);
      const inc = t.addIncident(e.lngLat.lat, e.lngLat.lng);
      t.setPlacementMode(false);
      useStore.getState().setSelectedIncident(inc.id);
    };
    map.on("click", handler);
    return () => {
      map.off("click", handler);
    };
  }, [mapLoaded]);

  // Crosshair cursor while placement is armed.
  const testPlacementMode = useTestMode((s) => s.placementMode);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.getCanvas().style.cursor = testPlacementMode ? "crosshair" : "";
  }, [testPlacementMode]);

  // Basemap swap: rather than calling setStyle (which wipes every user
  // source/layer), we install the satellite raster as an overlay layer
  // lazily — the first time the user actually clicks Satellite. The dark
  // vector style stays underneath untouched, so none of the data layers
  // (perimeter, incidents, evac, cone, routes, infra, wind) ever have to
  // re-attach when the user flips the basemap.
  //
  // Lazy install matters: adding the raster source at map init (even
  // hidden) makes MapLibre prefetch satellite tiles while the carto
  // basemap is still loading, which competes for the browser's per-host
  // connection slots and noticeably delays the initial dark render.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;
    if (basemap === "satellite" && !map.getLayer("satellite-overlay")) {
      try {
        map.addSource(
          "satellite-overlay",
          SATELLITE_STYLE.sources["esri-world-imagery"],
        );
        map.addLayer({
          id: "satellite-overlay",
          type: "raster",
          source: "satellite-overlay",
        });
      } catch (err) {
        console.warn("satellite overlay install error:", err);
      }
    }

    if (!map.getLayer("satellite-overlay")) return;

    map.setLayoutProperty(
      "satellite-overlay",
      "visibility",
      basemap === "satellite" ? "visible" : "none",
    );

    // On every basemap flip, promote our data layers above the satellite
    // raster. We detect "ours" by an allowlist of known prefixes — anything
    // outside that list is a Carto vector basemap layer (water, roads,
    // labels, etc.) and must stay underneath the raster, otherwise carto
    // geometry paints on top of satellite imagery and the user sees the
    // dark base bleeding through. Walking the live style on each flip means
    // layers added later (e.g. the dynamic `infra-*` ones) get lifted too.
    if (basemap === "satellite") {
      const OUR_LAYER_PREFIXES = [
        "cone-",
        "evac-",
        "suggested-evac-",
        "accepted-evac-",
        "firms-",
        "incidents-",
        "perimeter-",
        "rally-points",
        "routes-",
        "staging-point",
        "infra-",
      ];
      const layers = map.getStyle().layers ?? [];
      for (const layer of layers) {
        const id = layer.id;
        if (!OUR_LAYER_PREFIXES.some((p) => id.startsWith(p))) continue;
        try {
          map.moveLayer(id);
        } catch {
          /* layer may have been removed mid-flip */
        }
      }
    }
  }, [basemap, mapLoaded]);

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

  // Enable the perimeter session whenever a fire is (re-)selected.
  useEffect(() => {
    setPerimeterEnabled(!!selectedIncidentId);
  }, [selectedIncidentId]);

  // Dismiss the perimeter when the user zooms back out below the threshold.
  // Crucially we only dismiss on USER-initiated zoom (wheel, pinch) — not
  // on the parabolic dip MapLibre's flyTo performs mid-animation, which
  // otherwise hides the perimeter on the new fire when switching between
  // two already-zoomed-in incidents.
  useEffect(() => {
    if (!abovePerimeterZoom && lastZoomFromUserRef.current) {
      setPerimeterEnabled(false);
    }
  }, [abovePerimeterZoom]);

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
      const coords = (e.features[0].geometry as GeoJSON.Point).coordinates as [
        number,
        number,
      ];
      // Always fly in on click — this handles re-clicking an already-selected
      // fire while zoomed out (setSelectedIncident would be a no-op for the
      // same ID so the fly-to useEffect wouldn't re-run without this).
      setSelectedIncident(id);
      setPerimeterEnabled(true); // restore perimeter session on every click
      map.flyTo({ center: coords, zoom: 11, duration: 1400 });
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

  // ---- Spread prediction cone (high-vis purple, tornado-warning style) ----
  // The spread agent now publishes a server-side Minkowski-swept polygon at
  // `swept_cone_24h` that already extends the WFIGS perimeter forward
  // through the 24h cone. Prefer it so the painted region exactly matches
  // the polygon the impact queries (population + critical infra) ran
  // against. Fall back to the raw `cones["24h"]` ellipse only when the
  // server didn't publish a swept polygon.
  const cone24h = useMemo(() => {
    const payload = (spread?.payload ?? {}) as SpreadPayload;
    return payload.swept_cone_24h ?? payload.cones?.["24h"] ?? null;
  }, [spread]);

  // Did the server already do the perimeter sweep? When yes we render the
  // polygon directly; when no, we still attempt a client-side sweep below
  // so older payloads keep working.
  const coneIsServerSwept = useMemo(() => {
    const payload = (spread?.payload ?? {}) as SpreadPayload;
    return payload.swept_cone_24h != null;
  }, [spread]);

  const coneImpact = useMemo<ConeImpact | null>(() => {
    const payload = (spread?.payload ?? {}) as SpreadPayload;
    const impact = payload.cone_impact;
    if (!impact || impact.error) return null;
    return impact;
  }, [spread]);

  // ---- Synchronise circle visibility & perimeter visibility ----
  // The two layers are always toggled together so the transition is atomic:
  // circle hidden  ↔  perimeter visible   (when perimeterEnabled && zoomed in)
  // circle visible ↔  perimeter hidden    (any other state)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    const hasPerimeter = !!perimeter?.features?.length;
    // Both conditions must hold: the session is active AND zoom is above the
    // threshold AND perimeter data has actually arrived.
    const perimeterOn = perimeterEnabled && abovePerimeterZoom && hasPerimeter;

    // --- circle layers: exclude the selected fire when perimeter is on ---
    const circleFilter: maplibregl.FilterSpecification | null = perimeterOn
      ? (["!=", ["get", "id"], selectedIncidentId] as maplibregl.FilterSpecification)
      : null;
    try {
      if (map.getLayer("incidents-glow")) map.setFilter("incidents-glow", circleFilter);
      if (map.getLayer("incidents-circle")) map.setFilter("incidents-circle", circleFilter);
    } catch (err) {
      console.warn("circle filter error:", err);
    }

    // --- perimeter layers: show only while perimeterOn ---
    const perimVis = perimeterOn ? "visible" : "none";
    try {
      if (map.getLayer("perimeter-fill")) map.setLayoutProperty("perimeter-fill", "visibility", perimVis);
      if (map.getLayer("perimeter-outline")) map.setLayoutProperty("perimeter-outline", "visibility", perimVis);
    } catch (err) {
      console.warn("perimeter visibility error:", err);
    }
  }, [perimeterEnabled, abovePerimeterZoom, perimeter, selectedIncidentId, mapLoaded]);

  // ---- Render spread prediction cone ----
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

    // If the server already published a swept polygon, use it verbatim — it's
    // the exact geometry the impact queries ran against. Otherwise fall back
    // to the client-side Minkowski-sum hull so older payloads still render.
    const hullCone = coneIsServerSwept
      ? cone24h
      : buildPerimeterCone(
          cone24h,
          perimeter ?? null,
          selectedIncident?.lon ?? null,
          selectedIncident?.lat ?? null,
        );

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
      // Register diagonal orange/white stripe pattern (once per style).
      if (!map.hasImage("cone-stripes")) {
        const size = 16;
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, size, size);
          ctx.strokeStyle = "#f97316";
          ctx.lineWidth = 5;
          ctx.lineCap = "square";
          ctx.beginPath();
          for (let i = -size; i < size * 2; i += 8) {
            ctx.moveTo(i, 0);
            ctx.lineTo(i + size, size);
          }
          ctx.stroke();
          try {
            map.addImage(
              "cone-stripes",
              ctx.getImageData(0, 0, size, size),
              { pixelRatio: 2 },
            );
          } catch {
            /* image may have been added between check and add */
          }
        }
      }
      // Soft outer halo for contrast on dark basemap.
      map.addLayer({
        id: "cone-outline-glow",
        type: "line",
        source: "cone",
        paint: {
          "line-color": "#f97316",
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
          "fill-pattern": "cone-stripes",
          "fill-opacity": 0.6,
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
          "line-color": "#f97316",
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
  }, [cone24h, coneImpact, coneIsServerSwept, perimeter, selectedIncident, showCone, mapLoaded]);

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

  // ---- Suggested evac zones (AI proposals, pulsing) ----
  // Yellow pulse = WARNING suggestion. Red pulse = ORDER suggestion.
  // Lives on the map only while the corresponding interrupt is pending in
  // the approval queue. On approve, the polygon moves to acceptedEvacZones
  // (rendered as a solid layer below).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    const SOURCE_ID = "suggested-evac";
    const FILL_ID = "suggested-evac-fill";
    const LINE_ID = "suggested-evac-line";

    try {
      if (map.getLayer(FILL_ID)) map.removeLayer(FILL_ID);
      if (map.getLayer(LINE_ID)) map.removeLayer(LINE_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    } catch {
      /* mid-rerender */
    }

    if (!suggestedEvacZones.length) return;

    const features: GeoJSON.Feature[] = suggestedEvacZones.map((z) => ({
      type: "Feature",
      geometry: z.geom,
      properties: {
        zone_id: z.zone_id,
        name: z.name,
        status: z.status,
      },
    }));
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features,
    };

    try {
      map.addSource(SOURCE_ID, { type: "geojson", data: fc });
      const colorExpr = [
        "match",
        ["get", "status"],
        "ORDER",
        "#dc2626",
        "WARNING",
        "#facc15",
        "#facc15",
      ] as unknown as maplibregl.ExpressionSpecification;

      map.addLayer({
        id: FILL_ID,
        type: "fill",
        source: SOURCE_ID,
        paint: {
          "fill-color": colorExpr,
          "fill-opacity": 0.25,
        },
      });
      map.addLayer({
        id: LINE_ID,
        type: "line",
        source: SOURCE_ID,
        paint: {
          "line-color": colorExpr,
          "line-width": 2,
          "line-opacity": 0.9,
          "line-dasharray": [2, 1.5],
        },
      });

      // Pulse: ramp fill-opacity + line-width on a 1.4s cycle. Pure paint
      // updates — no source mutation — so this is cheap.
      let raf = 0;
      const start = performance.now();
      const tick = (now: number) => {
        const t = ((now - start) / 1400) % 1;
        // Smooth ease in/out via a cosine wave: 0 → 1 → 0 across the cycle.
        const wave = (1 - Math.cos(t * Math.PI * 2)) / 2;
        const fillOpacity = 0.18 + 0.30 * wave;
        const lineWidth = 1.8 + 2.6 * wave;
        try {
          if (map.getLayer(FILL_ID)) {
            map.setPaintProperty(FILL_ID, "fill-opacity", fillOpacity);
          }
          if (map.getLayer(LINE_ID)) {
            map.setPaintProperty(LINE_ID, "line-width", lineWidth);
          }
        } catch {
          /* layer torn down */
        }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);

      // Hover popup so users can see which suggestion they're inspecting.
      const showPopup = (
        e: maplibregl.MapMouseEvent & {
          features?: maplibregl.MapGeoJSONFeature[];
        },
      ) => {
        if (!popupRef.current || !e.features?.length) return;
        const p = e.features[0].properties as Record<string, unknown>;
        const status = String(p.status ?? "");
        const color = status === "ORDER" ? "#dc2626" : "#facc15";
        popupRef.current
          .setLngLat(e.lngLat)
          .setHTML(
            `<div style="font-size:12px;line-height:1.5;color:#0f172a;background:${color};padding:6px 8px;border-radius:6px;font-weight:600">
              SUGGESTED ${status}<br/>
              <span style="font-weight:400">${escapeHtml(String(p.name ?? ""))}</span><br/>
              <span style="font-size:10px;font-weight:500">awaiting IC approval</span>
            </div>`,
          )
          .addTo(map);
        map.getCanvas().style.cursor = "pointer";
      };
      const hidePopup = () => {
        popupRef.current?.remove();
        map.getCanvas().style.cursor = "";
      };
      map.on("mousemove", FILL_ID, showPopup);
      map.on("mouseleave", FILL_ID, hidePopup);

      // Keep perimeter + incidents above suggestion overlay.
      if (map.getLayer("perimeter-fill")) map.moveLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.moveLayer("perimeter-outline");
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");

      return () => {
        cancelAnimationFrame(raf);
        try {
          map.off("mousemove", FILL_ID, showPopup);
          map.off("mouseleave", FILL_ID, hidePopup);
        } catch {
          /* ignore */
        }
      };
    } catch (err) {
      console.warn("suggested-evac layer error:", err);
    }
  }, [suggestedEvacZones, mapLoaded]);

  // ---- Accepted evac zones (post-approval, solid) ----
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    const SOURCE_ID = "accepted-evac";
    const FILL_ID = "accepted-evac-fill";
    const LINE_ID = "accepted-evac-line";

    try {
      if (map.getLayer(FILL_ID)) map.removeLayer(FILL_ID);
      if (map.getLayer(LINE_ID)) map.removeLayer(LINE_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    } catch {
      /* mid-rerender */
    }

    if (!acceptedEvacZones.length) return;

    const features: GeoJSON.Feature[] = acceptedEvacZones.map((z) => ({
      type: "Feature",
      geometry: z.polygon,
      properties: { zone_id: z.zone_id, name: z.name, status: z.status },
    }));
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features,
    };

    try {
      map.addSource(SOURCE_ID, { type: "geojson", data: fc });
      const colorExpr = [
        "match",
        ["get", "status"],
        "ORDER",
        "#dc2626",
        "WARNING",
        "#facc15",
        "#facc15",
      ] as unknown as maplibregl.ExpressionSpecification;

      map.addLayer({
        id: FILL_ID,
        type: "fill",
        source: SOURCE_ID,
        paint: {
          "fill-color": colorExpr,
          "fill-opacity": 0.38,
        },
      });
      map.addLayer({
        id: LINE_ID,
        type: "line",
        source: SOURCE_ID,
        paint: {
          "line-color": colorExpr,
          "line-width": 2.5,
          "line-opacity": 1,
        },
      });

      if (map.getLayer("perimeter-fill")) map.moveLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.moveLayer("perimeter-outline");
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");
    } catch (err) {
      console.warn("accepted-evac layer error:", err);
    }
  }, [acceptedEvacZones, mapLoaded]);

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

  // ---- Agent-computed routes + staging marker ----
  // `routing_staging.payload.primary_routes` = candidate-staging → incident
  //   (firefighter ingress). Rendered dashed amber.
  // `routing_staging.payload.egress_routes` = incident → nearest major-road
  //   node in N/E/S/W (civilian egress / pushed-out fallback). Solid red.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    const LAYER_IDS = [
      "routes-egress",
      "routes-egress-casing",
      "routes-ingress",
      "routes-ingress-casing",
      "staging-point",
      "rally-points",
      "rally-points-label",
    ];
    const SOURCE_IDS = [
      "routes-ingress",
      "routes-egress",
      "staging",
      "rally-points",
    ];
    try {
      for (const id of LAYER_IDS) {
        if (map.getLayer(id)) map.removeLayer(id);
      }
      for (const id of SOURCE_IDS) {
        if (map.getSource(id)) map.removeSource(id);
      }
    } catch {
      /* map may be mid-rerender */
    }

    if (!showRoutes) return;
    const payload = routingOutput?.payload;
    if (!payload) return;

    const toLineString = (
      path: Array<[number, number]> | undefined,
      props: Record<string, unknown>,
    ): GeoJSON.Feature | null => {
      if (!path || path.length < 2) return null;
      // Agent emits [lat, lon]; GeoJSON wants [lon, lat].
      const coords = path.map(([lat, lon]) => [lon, lat] as [number, number]);
      return {
        type: "Feature",
        geometry: { type: "LineString", coordinates: coords },
        properties: props,
      };
    };

    const ingressFeatures = (payload.primary_routes ?? [])
      .map((r, i) =>
        toLineString(r.path, {
          kind: "ingress",
          rank: i,
          length_km: r.length_km ?? null,
          minutes: r.est_drive_minutes ?? null,
        }),
      )
      .filter((f): f is GeoJSON.Feature => f !== null);

    const egressFeatures = (payload.egress_routes ?? [])
      .map((r) =>
        toLineString(r.path, {
          kind: "egress",
          bearing: r.bearing ?? "?",
          bearing_deg: r.bearing_deg ?? null,
          length_km: r.length_km ?? null,
          minutes: r.est_drive_minutes ?? null,
          wind_relation: r.wind_relation ?? "unknown",
          dest_name: r.destination?.name ?? null,
          dest_type: r.destination?.rally_type ?? null,
          dest_source: r.destination?.source ?? null,
        }),
      )
      .filter((f): f is GeoJSON.Feature => f !== null);

    try {
      if (egressFeatures.length) {
        map.addSource("routes-egress", {
          type: "geojson",
          data: { type: "FeatureCollection", features: egressFeatures },
        });
        map.addLayer({
          id: "routes-egress-casing",
          type: "line",
          source: "routes-egress",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#0c0a09",
            "line-width": 6,
            "line-opacity": 0.7,
          },
        });
        map.addLayer({
          id: "routes-egress",
          type: "line",
          source: "routes-egress",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            // Color by wind relation: upwind (safe escape) = green,
            // crosswind (flank) = amber, downwind (into fire-head) = red,
            // unknown (no wind data) = neutral grey.
            "line-color": [
              "match",
              ["get", "wind_relation"],
              "upwind",
              "#10b981",
              "crosswind",
              "#f59e0b",
              "downwind",
              "#dc2626",
              "#94a3b8",
            ],
            "line-width": 3.2,
            "line-opacity": 0.95,
          },
        });
      }

      if (ingressFeatures.length) {
        map.addSource("routes-ingress", {
          type: "geojson",
          data: { type: "FeatureCollection", features: ingressFeatures },
        });
        map.addLayer({
          id: "routes-ingress-casing",
          type: "line",
          source: "routes-ingress",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#0c0a09",
            "line-width": 5,
            "line-opacity": 0.6,
          },
        });
        map.addLayer({
          id: "routes-ingress",
          type: "line",
          source: "routes-ingress",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": "#fbbf24",
            "line-width": 2.4,
            "line-opacity": [
              "case",
              ["==", ["get", "rank"], 0],
              0.95,
              0.55,
            ],
            "line-dasharray": [2, 1.5],
          },
        });
      }

      const top = payload.candidates?.[0];
      if (top?.loc && Array.isArray(top.loc) && top.loc.length === 2) {
        const [lat, lon] = top.loc;
        map.addSource("staging", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [lon, lat] },
                properties: {
                  name: top.name ?? "Staging",
                  score: top.score ?? null,
                },
              },
            ],
          },
        });
        map.addLayer({
          id: "staging-point",
          type: "circle",
          source: "staging",
          paint: {
            "circle-radius": 7,
            "circle-color": "#22c55e",
            "circle-stroke-color": "#052e16",
            "circle-stroke-width": 2,
          },
        });

        const stagingPopupHtml = buildStagingPopupHtml(top, payload.wind);
        const showStagingPopup = () => {
          if (!popupRef.current) return;
          popupRef.current
            .setLngLat([lon, lat])
            .setHTML(stagingPopupHtml)
            .addTo(map);
          map.getCanvas().style.cursor = "pointer";
        };
        const hideStagingPopup = () => {
          popupRef.current?.remove();
          map.getCanvas().style.cursor = "";
        };
        map.on("mouseenter", "staging-point", showStagingPopup);
        map.on("mouseleave", "staging-point", hideStagingPopup);
      }

      // Hover popups for egress routes — show bearing + wind relation +
      // drive time + destination rally point (if routed to one).
      if (egressFeatures.length) {
        const showEgressPopup = (e: maplibregl.MapMouseEvent) => {
          if (!popupRef.current) return;
          const feature = (
            e as unknown as { features?: GeoJSON.Feature[] }
          ).features?.[0];
          const props = feature?.properties as
            | {
                bearing?: string;
                bearing_deg?: number;
                length_km?: number;
                minutes?: number;
                wind_relation?: string;
                dest_name?: string;
                dest_type?: string;
                dest_source?: string;
              }
            | undefined;
          if (!props) return;
          popupRef.current
            .setLngLat(e.lngLat)
            .setHTML(buildEgressPopupHtml(props))
            .addTo(map);
          map.getCanvas().style.cursor = "pointer";
        };
        const hideEgressPopup = () => {
          popupRef.current?.remove();
          map.getCanvas().style.cursor = "";
        };
        map.on("mouseenter", "routes-egress", showEgressPopup);
        map.on("mousemove", "routes-egress", showEgressPopup);
        map.on("mouseleave", "routes-egress", hideEgressPopup);
      }

      // Rally-point pins — defined evacuation destinations (OSM
      // assembly_point/shelter/school/community_centre + HIFLD schools/EOCs
      // + CAL FIRE per-incident evac centers). Color encodes wind_relation
      // so the IC can see at a glance which destinations are upwind.
      const rallyFeatures: GeoJSON.Feature[] = (payload.rally_points ?? [])
        .map((rp): GeoJSON.Feature | null => {
          if (!rp.loc || rp.loc.length !== 2) return null;
          const [lat, lon] = rp.loc;
          return {
            type: "Feature",
            geometry: { type: "Point", coordinates: [lon, lat] },
            properties: {
              name: rp.name ?? "rally point",
              rally_type: rp.rally_type ?? "unknown",
              source: rp.source ?? "?",
              capacity: rp.capacity ?? null,
              score: rp.score ?? null,
              wind_relation: rp.wind_relation ?? "unknown",
            },
          };
        })
        .filter((f): f is GeoJSON.Feature => f !== null);

      if (rallyFeatures.length) {
        map.addSource("rally-points", {
          type: "geojson",
          data: { type: "FeatureCollection", features: rallyFeatures },
        });
        map.addLayer({
          id: "rally-points",
          type: "circle",
          source: "rally-points",
          paint: {
            "circle-radius": 6,
            "circle-color": [
              "match",
              ["get", "wind_relation"],
              "upwind",
              "#10b981",
              "crosswind",
              "#f59e0b",
              "downwind",
              "#dc2626",
              "#94a3b8",
            ],
            "circle-stroke-color": "#0c0a09",
            "circle-stroke-width": 1.5,
            "circle-opacity": 0.95,
          },
        });

        const showRallyPopup = (e: maplibregl.MapMouseEvent) => {
          if (!popupRef.current) return;
          const feature = (
            e as unknown as { features?: GeoJSON.Feature[] }
          ).features?.[0];
          const props = feature?.properties as
            | {
                name?: string;
                rally_type?: string;
                source?: string;
                capacity?: number | null;
                score?: number;
                wind_relation?: string;
              }
            | undefined;
          if (!props) return;
          popupRef.current
            .setLngLat(e.lngLat)
            .setHTML(buildRallyPopupHtml(props))
            .addTo(map);
          map.getCanvas().style.cursor = "pointer";
        };
        const hideRallyPopup = () => {
          popupRef.current?.remove();
          map.getCanvas().style.cursor = "";
        };
        map.on("mouseenter", "rally-points", showRallyPopup);
        map.on("mousemove", "rally-points", showRallyPopup);
        map.on("mouseleave", "rally-points", hideRallyPopup);
      }

      // Re-stack: cone < perimeter < incidents on top of routes so the
      // spread cone, fire perimeter, and incident markers all stay
      // legible whether the basemap is the dark vector style or the
      // satellite imagery.
      if (map.getLayer("cone-outline-glow")) map.moveLayer("cone-outline-glow");
      if (map.getLayer("cone-fill")) map.moveLayer("cone-fill");
      if (map.getLayer("cone-outline")) map.moveLayer("cone-outline");
      if (map.getLayer("perimeter-fill")) map.moveLayer("perimeter-fill");
      if (map.getLayer("perimeter-outline")) map.moveLayer("perimeter-outline");
      if (map.getLayer("incidents-glow")) map.moveLayer("incidents-glow");
      if (map.getLayer("incidents-circle")) map.moveLayer("incidents-circle");
    } catch (err) {
      console.warn("routes layer error:", err);
    }
  }, [routingOutput, showRoutes, mapLoaded]);

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
      // Open-Meteo's `wind_direction_10m` is the meteorological FROM bearing
      // (compass degrees the wind originates from). maplibre-gl-wind's
      // generateWindTexture takes the TO heading — u = speed·sin(dir),
      // v = speed·cos(dir) means `direction: 0` yields v=+speed (northward
      // flow), which is the TO convention. Flip FROM → TO by adding 180°
      // before handing the vectors off. Previously the flipped array was
      // computed but discarded — the raw FROM was passed instead, so
      // particles drifted straight upwind.
      const vectorsTo = wind.vectors.map((v) => ({
        ...v,
        direction: (v.direction + 180) % 360,
      }));
      // When zoomed out the 66km sample box collapses to a few pixels, so we
      // pad the rendered bounds outward. The IDW texture extrapolates the same
      // 7x7 sample to the wider extent — fine for visualization since wind at
      // this synoptic scale is smooth.
      const [w0, s0, e0, n0] = wind.bounds;
      const padX = (e0 - w0) * (windLowZoom ? 1.0 : 0.0);
      const padY = (n0 - s0) * (windLowZoom ? 1.0 : 0.0);
      const renderBounds: [number, number, number, number] = [
        w0 - padX,
        s0 - padY,
        e0 + padX,
        n0 + padY,
      ];

      const { canvas, uMin, uMax, vMin, vMax } = generateWindTexture(
        vectorsTo,
        {
          width: 128,
          height: 128,
          bounds: renderBounds,
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
            bounds: renderBounds,
            imageUnscale: [minV, maxV],
            // Windfinder/earth.nullschool aesthetic: lots of thin, fast
            // particles drawing crisp streamlines. Per-tick stride is
            // (speedFactor * 0.01) / 2^zoom and the prop min/max are
            // deck.gl validators (not runtime caps), so we push values
            // past their advisory ranges for the look we want.
            // Density scales up at low zoom so the smaller box still feels
            // alive; width grows just enough to stay legible.
            numParticles: windLowZoom ? 1800 : 1200,
            maxAge: 1100,
            speedFactor: 52,
            width: windLowZoom ? 1.6 : 1.1,
            // Tight speed range so typical 2–10 m/s winds land in the
            // bright white band; only genuinely strong gusts trip warm.
            speedRange: [0, 14],
            // Mostly translucent → bright white core, with a faint warm
            // wash reserved for the top end. Keeps the field reading as a
            // cohesive flow instead of a rainbow.
            colorRamp: [
              [0.0, [226, 232, 240, 0]], // fully transparent tail
              [0.1, [241, 245, 249, 140]], // soft slate fade-in
              [0.35, [255, 255, 255, 235]], // bright white core
              [0.6, [255, 255, 255, 255]], // peak white
              [0.8, [253, 186, 116, 245]], // orange-300 (breezy)
              [1.0, [239, 68, 68, 255]], // red-500 (gale)
            ],
          }),
        ],
      });
      map.addControl(overlay);
      deckOverlayRef.current = overlay;
    } catch (err) {
      console.warn("wind layer error:", err);
    }
  }, [wind, showWind, mapLoaded, windLowZoom]);

  // ---- Critical infrastructure overlays ----
  // One driver effect handles all INFRA_LAYERS. We tear down & re-add per
  // layer on every change to data/visibility/mapLoaded; idempotent per
  // layer via stable id-prefixed source/layer names.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapLoaded) return;

    for (const { layer, data } of infraResults) {
      const sourceId = `infra-${layer.id}`;
      const pointLayerId = `infra-${layer.id}-pt`;
      const haloLayerId = `infra-${layer.id}-halo`;
      const lineLayerId = `infra-${layer.id}-line`;
      const lineCasingId = `infra-${layer.id}-line-casing`;

      try {
        if (map.getLayer(pointLayerId)) map.removeLayer(pointLayerId);
        if (map.getLayer(haloLayerId)) map.removeLayer(haloLayerId);
        if (map.getLayer(lineLayerId)) map.removeLayer(lineLayerId);
        if (map.getLayer(lineCasingId)) map.removeLayer(lineCasingId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
      } catch {
        /* mid-rerender */
      }

      if (!infraVisibility[layer.id]) continue;
      if (!data?.features?.length) continue;

      try {
        map.addSource(sourceId, { type: "geojson", data });
        if (layer.geometry === "line") {
          map.addLayer({
            id: lineCasingId,
            type: "line",
            source: sourceId,
            layout: { "line-cap": "round", "line-join": "round" },
            paint: {
              "line-color": "#0c0a09",
              "line-width": 4,
              "line-opacity": 0.6,
            },
          });
          map.addLayer({
            id: lineLayerId,
            type: "line",
            source: sourceId,
            layout: { "line-cap": "round", "line-join": "round" },
            paint: {
              "line-color": layer.color,
              "line-width": 1.8,
              "line-opacity": 0.9,
            },
          });
        } else {
          // Halo for legibility on busy satellite imagery.
          map.addLayer({
            id: haloLayerId,
            type: "circle",
            source: sourceId,
            paint: {
              "circle-color": layer.color,
              "circle-radius": 7,
              "circle-blur": 0.6,
              "circle-opacity": 0.4,
            },
          });
          map.addLayer({
            id: pointLayerId,
            type: "circle",
            source: sourceId,
            paint: {
              "circle-color": layer.color,
              "circle-radius": 4,
              "circle-stroke-color": "#0c0a09",
              "circle-stroke-width": 1.5,
              "circle-opacity": 0.95,
            },
          });

          // Hover popup with whatever name the source carries.
          const onMove = (
            e: maplibregl.MapMouseEvent & {
              features?: maplibregl.MapGeoJSONFeature[];
            },
          ) => {
            if (!popupRef.current || !e.features?.length) return;
            const p = e.features[0].properties as Record<string, unknown>;
            const name =
              (p?.NAME as string) ??
              (p?.name as string) ??
              (p?.Licensee as string) ??
              (p?.operator as string) ??
              layer.label;
            const detailEntries: string[] = [];
            const beds = p?.BEDS ?? p?.beds;
            if (beds && Number(beds) > 0)
              detailEntries.push(`${beds} beds`);
            const enr = p?.ENROLLMENT ?? p?.enrollment;
            if (enr && Number(enr) > 0)
              detailEntries.push(`${enr} students`);
            const city = p?.CITY ?? p?.city ?? p?.LocCity;
            if (city) detailEntries.push(String(city));
            const coords = (
              e.features[0].geometry as GeoJSON.Point
            ).coordinates as [number, number];
            popupRef.current
              .setLngLat(coords)
              .setHTML(
                `<div style="font-size:12px;line-height:1.5;color:#e2e8f0;background:#1e293b;padding:6px 8px;border-radius:6px;border:1px solid ${layer.color}66">
                  <strong style="color:${layer.color}">${escapeHtml(String(name))}</strong>
                  <br/><span style="color:#94a3b8">${layer.label}${detailEntries.length ? " · " + detailEntries.join(" · ") : ""}</span>
                </div>`,
              )
              .addTo(map);
            map.getCanvas().style.cursor = "pointer";
          };
          const onLeave = () => {
            popupRef.current?.remove();
            map.getCanvas().style.cursor = "";
          };
          map.on("mousemove", pointLayerId, onMove);
          map.on("mouseleave", pointLayerId, onLeave);
        }
      } catch (err) {
        console.warn(`infra layer ${layer.id} error:`, err);
      }
    }

    // Keep operational layers (cone, perimeter, routes, incidents) above
    // the infra dots so the fire-front context is never obscured.
    const liftIfPresent = (id: string) => {
      if (map.getLayer(id)) map.moveLayer(id);
    };
    liftIfPresent("cone-outline-glow");
    liftIfPresent("cone-fill");
    liftIfPresent("cone-outline");
    liftIfPresent("routes-egress-casing");
    liftIfPresent("routes-egress");
    liftIfPresent("routes-ingress-casing");
    liftIfPresent("routes-ingress");
    liftIfPresent("staging-point");
    liftIfPresent("perimeter-fill");
    liftIfPresent("perimeter-outline");
    liftIfPresent("incidents-glow");
    liftIfPresent("incidents-circle");
  }, [infraResults, infraVisibility, mapLoaded]);

  return (
    <div className="relative h-full w-full">
      <div ref={containerRef} className="absolute inset-0" />
      <Legend
        hasPerimeter={perimeterEnabled && abovePerimeterZoom && !!perimeter?.features?.length}
        showWind={showWind}
        setShowWind={setShowWind}
        showEvac={showEvac}
        setShowEvac={setShowEvac}
        showFirms={showFirms}
        setShowFirms={setShowFirms}
        showRoutes={showRoutes}
        setShowRoutes={setShowRoutes}
        basemap={basemap}
        setBasemap={setBasemap}
        showCone={showCone}
        setShowCone={setShowCone}
        hasCone={!!cone24h}
        wind={wind}
        evacCount={evacFiltered?.features.length ?? 0}
        firmsCount={firms?.features?.length ?? 0}
        ingressCount={routingOutput?.payload?.primary_routes?.length ?? 0}
        egressCount={routingOutput?.payload?.egress_routes?.length ?? 0}
        infraVisibility={infraVisibility}
        toggleInfra={toggleInfra}
        infraCounts={Object.fromEntries(
          infraResults.map((r) => [r.layer.id, r.data?.features?.length ?? 0]),
        )}
        infraEnabled={selectedIncident != null}
        collapsed={legendCollapsed}
        setCollapsed={setLegendCollapsed}
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

// Cap how many perimeter vertices feed the Minkowski sum so we don't blow up
// (perimeter * cone vertices) for high-resolution WFIGS polygons.
const MAX_PERIM_VERTICES = 96;

function buildPerimeterCone(
  cone: GeoJSON.Polygon | GeoJSON.MultiPolygon,
  perimeter: GeoJSON.FeatureCollection | null,
  incidentLon: number | null,
  incidentLat: number | null,
): GeoJSON.Polygon {
  const coneVerts: Array<[number, number]> = [];
  collectVertices(cone, coneVerts);

  const perimVerts: Array<[number, number]> = [];
  if (perimeter?.features?.length) {
    for (const f of perimeter.features) {
      const g = f.geometry;
      if (g?.type === "Polygon" || g?.type === "MultiPolygon") {
        collectVertices(g, perimVerts);
      }
    }
  }

  // Without a perimeter (or an incident anchor) we can't do the Minkowski
  // sweep, so fall back to the bare cone hull.
  if (
    perimVerts.length === 0 ||
    incidentLon == null ||
    incidentLat == null
  ) {
    return { type: "Polygon", coordinates: [convexHull(coneVerts)] };
  }

  // The agent builds the cone with its rear vertex at the incident point.
  // Cone-offsets relative to that anchor are what we sweep around the
  // perimeter (Minkowski kernel).
  const coneOffsets: Array<[number, number]> = coneVerts.map(([lo, la]) => [
    lo - incidentLon,
    la - incidentLat,
  ]);

  // Subsample dense perimeters so vertex-pair count stays bounded.
  const stride = Math.max(
    1,
    Math.floor(perimVerts.length / MAX_PERIM_VERTICES),
  );
  const sampled: Array<[number, number]> = [];
  for (let i = 0; i < perimVerts.length; i += stride) sampled.push(perimVerts[i]);

  // Minkowski-sum vertex set: every cone offset translated to every
  // sampled perimeter vertex. Convex-hulling this gives a shape whose rear
  // matches the perimeter's downwind footprint and whose forward extent is
  // the perimeter shape swept along the cone — i.e., the perimeter
  // physically extended in the spread direction.
  const swept: Array<[number, number]> = [];
  for (const [px, py] of sampled) {
    for (const [dx, dy] of coneOffsets) {
      swept.push([px + dx, py + dy]);
    }
  }
  // Keep the original perimeter vertices so the rear edge clings to the
  // current fire outline even when the cone has degenerate zero-area bands.
  for (const v of perimVerts) swept.push(v);

  return { type: "Polygon", coordinates: [convexHull(swept)] };
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

// Component-score row in the staging popup. Renders a small 0..1 bar so the
// IC can see at a glance which axis is dragging the composite up or down.
function componentRow(label: string, value: number | undefined): string {
  const v = typeof value === "number" ? Math.max(0, Math.min(1, value)) : 0;
  const pct = (v * 100).toFixed(0);
  const color =
    v >= 0.7 ? "#10b981" : v >= 0.4 ? "#f59e0b" : "#dc2626";
  return `
    <div style="display:flex;align-items:center;gap:6px;margin:2px 0">
      <span style="width:54px;color:#94a3b8;font-size:10px">${escapeHtml(label)}</span>
      <span style="flex:1;height:5px;background:#0f172a;border-radius:2px;overflow:hidden">
        <span style="display:block;height:100%;width:${pct}%;background:${color}"></span>
      </span>
      <span style="width:30px;text-align:right;font-variant-numeric:tabular-nums;font-size:10px;color:#cbd5e1">${v.toFixed(2)}</span>
    </div>`;
}

function buildStagingPopupHtml(
  top: {
    name?: string;
    score?: number;
    dist_incident_km?: number;
    nearest_water_km?: number;
    score_components?: Record<string, number>;
    score_raw?: Record<string, number | null>;
  },
  wind?: { from_deg?: number | null; speed_mph?: number | null } | null,
): string {
  const components = top.score_components ?? {};
  const raw = top.score_raw ?? {};
  const compRows = [
    componentRow("incident", components.incident),
    componentRow("water", components.water),
    componentRow("station", components.station),
    componentRow("paved", components.paved),
    componentRow("elevation", components.elevation),
    componentRow("slope", components.slope),
    componentRow("wind", components.wind),
  ].join("");
  const distLine =
    top.dist_incident_km != null
      ? `${top.dist_incident_km.toFixed(1)} km from fire`
      : "";
  const waterLine =
    top.nearest_water_km != null
      ? ` · water ${top.nearest_water_km.toFixed(2)} km`
      : "";
  const slopeLine =
    raw.slope_pct != null ? ` · slope ${Number(raw.slope_pct).toFixed(1)}%` : "";
  const elevLine =
    raw.elevation_m != null
      ? ` · ${Number(raw.elevation_m).toFixed(0)} m`
      : "";
  const windLine =
    wind && wind.from_deg != null
      ? `<div style="color:#94a3b8;font-size:10px;margin-top:2px">wind from ${Number(wind.from_deg).toFixed(0)}°${wind.speed_mph != null ? ` @ ${Number(wind.speed_mph).toFixed(0)} mph` : ""}</div>`
      : "";
  return `<div style="font-size:11px;line-height:1.4;color:#e2e8f0;background:#1e293b;padding:8px 10px;border-radius:6px;border:1px solid #22c55e66;min-width:220px">
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:6px">
      <strong style="color:#22c55e">Proposed staging</strong>
      <span style="font-variant-numeric:tabular-nums;color:#cbd5e1">${
        top.score != null ? Number(top.score).toFixed(2) : "?"
      }</span>
    </div>
    <div style="color:#e2e8f0;margin-top:2px">${escapeHtml(String(top.name ?? "Staging"))}</div>
    <div style="color:#94a3b8;font-size:10px">${escapeHtml(distLine + waterLine + slopeLine + elevLine)}</div>
    ${windLine}
    <div style="border-top:1px solid #334155;margin:6px 0 2px 0"></div>
    ${compRows}
  </div>`;
}

function buildEgressPopupHtml(props: {
  bearing?: string;
  bearing_deg?: number;
  length_km?: number;
  minutes?: number;
  wind_relation?: string;
  dest_name?: string;
  dest_type?: string;
  dest_source?: string;
}): string {
  const rel = (props.wind_relation ?? "unknown").toLowerCase();
  const relColor = windRelationHex(rel);
  const relText =
    rel === "upwind"
      ? "upwind · safe escape"
      : rel === "crosswind"
        ? "crosswind · flank"
        : rel === "downwind"
          ? "downwind · AVOID (into fire-head)"
          : "wind relation unknown";
  const lenLine =
    props.length_km != null
      ? `${Number(props.length_km).toFixed(1)} km`
      : "—";
  const minLine =
    props.minutes != null ? `${Number(props.minutes).toFixed(0)} min` : "—";
  const bearingLine = `bearing ${escapeHtml(String(props.bearing ?? "?"))}${
    props.bearing_deg != null ? ` (${Number(props.bearing_deg).toFixed(0)}°)` : ""
  }`;
  const destBlock = props.dest_name
    ? `<div style="color:#e2e8f0;font-size:11px;margin-top:3px">→ ${escapeHtml(String(props.dest_name))}</div>
       <div style="color:#94a3b8;font-size:10px">${escapeHtml(String(props.dest_type ?? ""))}${props.dest_source ? ` · ${escapeHtml(String(props.dest_source))}` : ""}</div>`
    : `<div style="color:#94a3b8;font-size:10px;margin-top:3px;font-style:italic">no defined rally point — bearing fallback</div>`;
  return `<div style="font-size:11px;line-height:1.4;color:#e2e8f0;background:#1e293b;padding:6px 8px;border-radius:6px;border:1px solid ${relColor}66;min-width:200px">
    <strong style="color:${relColor}">Egress · ${escapeHtml(String(props.bearing ?? "?"))}</strong>
    ${destBlock}
    <div style="color:#cbd5e1;margin-top:3px">${escapeHtml(bearingLine)}</div>
    <div style="color:#cbd5e1">${escapeHtml(lenLine + " · " + minLine)}</div>
    <div style="color:${relColor};font-size:10px;margin-top:2px">${escapeHtml(relText)}</div>
  </div>`;
}

function buildRallyPopupHtml(props: {
  name?: string;
  rally_type?: string;
  source?: string;
  capacity?: number | null;
  score?: number;
  wind_relation?: string;
}): string {
  const rel = (props.wind_relation ?? "unknown").toLowerCase();
  const relColor = windRelationHex(rel);
  const typeLabel = (props.rally_type ?? "rally").replace(/_/g, " ");
  const capLine =
    props.capacity != null && Number(props.capacity) > 0
      ? `capacity ~${Number(props.capacity).toLocaleString()}`
      : "capacity unknown";
  const scoreLine =
    props.score != null ? `score ${Number(props.score).toFixed(2)}` : "";
  const srcLine = props.source ? `source: ${escapeHtml(String(props.source))}` : "";
  return `<div style="font-size:11px;line-height:1.4;color:#e2e8f0;background:#1e293b;padding:6px 8px;border-radius:6px;border:1px solid ${relColor}66;min-width:180px">
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:6px">
      <strong style="color:${relColor}">${escapeHtml(String(props.name ?? "Rally point"))}</strong>
      <span style="color:#cbd5e1;font-variant-numeric:tabular-nums">${escapeHtml(scoreLine)}</span>
    </div>
    <div style="color:#94a3b8;font-size:10px;text-transform:uppercase;letter-spacing:0.04em">${escapeHtml(typeLabel)}</div>
    <div style="color:#cbd5e1;margin-top:3px">${escapeHtml(capLine)}</div>
    <div style="color:#94a3b8;font-size:10px">${srcLine}</div>
    <div style="color:${relColor};font-size:10px;margin-top:3px">${escapeHtml(rel)}</div>
  </div>`;
}

function windRelationHex(rel: string): string {
  switch (rel) {
    case "upwind":
      return "#10b981";
    case "crosswind":
      return "#f59e0b";
    case "downwind":
      return "#dc2626";
    default:
      return "#94a3b8";
  }
}

function Legend({
  hasPerimeter,
  showWind,
  setShowWind,
  showEvac,
  setShowEvac,
  showFirms,
  setShowFirms,
  showRoutes,
  setShowRoutes,
  basemap,
  setBasemap,
  showCone,
  setShowCone,
  hasCone,
  wind,
  evacCount,
  firmsCount,
  ingressCount,
  egressCount,
  infraVisibility,
  toggleInfra,
  infraCounts,
  infraEnabled,
  collapsed,
  setCollapsed,
}: {
  hasPerimeter: boolean;
  showWind: boolean;
  setShowWind: (b: boolean) => void;
  showEvac: boolean;
  setShowEvac: (b: boolean) => void;
  showFirms: boolean;
  setShowFirms: (b: boolean) => void;
  showRoutes: boolean;
  setShowRoutes: (b: boolean) => void;
  basemap: Basemap;
  setBasemap: (b: Basemap) => void;
  showCone: boolean;
  setShowCone: (b: boolean) => void;
  hasCone: boolean;
  wind: WindGrid | undefined;
  evacCount: number;
  firmsCount: number;
  ingressCount: number;
  egressCount: number;
  infraVisibility: Record<string, boolean>;
  toggleInfra: (id: string) => void;
  infraCounts: Record<string, number>;
  infraEnabled: boolean;
  collapsed: boolean;
  setCollapsed: (b: boolean) => void;
}) {
  const center = wind?.vectors.length
    ? wind.vectors[Math.floor(wind.vectors.length / 2)]
    : null;
  return (
    // z-50 + isolate keeps the legend above the deck.gl wind overlay canvas,
    // which paints inside the maplibre container and would otherwise overlap
    // any sibling at z-20.
    <div className="absolute bottom-3 left-3 z-50 isolate max-w-[260px] rounded-md bg-smoke-800/90 text-[11px] text-smoke-200 shadow-lg backdrop-blur">
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex w-full items-center justify-between gap-2 rounded-t-md px-3 py-2 text-left text-smoke-200 hover:bg-smoke-700/60"
        aria-expanded={!collapsed}
      >
        <span className="font-semibold">Legend</span>
        <span
          className="text-smoke-400"
          aria-hidden
          style={{
            transform: collapsed ? "rotate(0deg)" : "rotate(180deg)",
            transition: "transform 120ms",
            display: "inline-block",
          }}
        >
          ▾
        </span>
      </button>
      {collapsed ? null : (
      <div className="px-3 pb-3">
      <div className="mb-1 font-semibold text-smoke-200">Basemap</div>
      <div className="mb-2 inline-flex overflow-hidden rounded border border-smoke-700">
        <button
          type="button"
          onClick={() => setBasemap("dark")}
          className={`px-2 py-0.5 text-[10px] ${
            basemap === "dark"
              ? "bg-ember-600 text-smoke-50"
              : "bg-smoke-900 text-smoke-300 hover:bg-smoke-800"
          }`}
        >
          Dark
        </button>
        <button
          type="button"
          onClick={() => setBasemap("satellite")}
          className={`px-2 py-0.5 text-[10px] ${
            basemap === "satellite"
              ? "bg-ember-600 text-smoke-50"
              : "bg-smoke-900 text-smoke-300 hover:bg-smoke-800"
          }`}
        >
          Satellite
        </button>
      </div>

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
          <ZoneSwatch color="#facc15" label="Warning" />
          <ZoneSwatch color="#a855f7" label="Shelter in place" />
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

      <label className="mt-2 flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={showRoutes}
          onChange={(e) => setShowRoutes(e.target.checked)}
          className="h-3 w-3 accent-ember-500"
        />
        <span className="font-medium">
          Agent routes ({ingressCount}+{egressCount})
        </span>
      </label>
      {showRoutes && (ingressCount > 0 || egressCount > 0) && (
        <div className="ml-5 mt-1 space-y-0.5 text-[10px] text-smoke-400">
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-5"
              style={{ borderTop: "2px solid #10b981" }}
            />
            <span>Egress · upwind (safe)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-5"
              style={{ borderTop: "2px solid #f59e0b" }}
            />
            <span>Egress · crosswind</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-5"
              style={{ borderTop: "2px solid #dc2626" }}
            />
            <span>Egress · downwind (avoid)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-5 border-t-2 border-dashed"
              style={{ borderColor: "#fbbf24" }}
            />
            <span>Ingress (staging → fire)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full bg-emerald-500" />
            <span>Proposed staging</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full border border-slate-900 bg-emerald-400" />
            <span>Rally point (color by wind)</span>
          </div>
        </div>
      )}
      {showRoutes && ingressCount + egressCount === 0 && (
        <div className="ml-5 mt-0.5 text-[10px] italic text-smoke-500">
          waiting on routing_staging agent…
        </div>
      )}

      <div className="mt-3 border-t border-smoke-700 pt-2">
        <div className="mb-1 font-semibold text-smoke-200">
          Critical infrastructure
        </div>
        {!infraEnabled ? (
          <div className="italic text-smoke-500">
            Select an incident to load nearby facilities (25 km AOI).
          </div>
        ) : (
          INFRA_GROUPS.map((g) => (
            <InfraGroup
              key={g.id}
              label={g.label}
              layers={INFRA_LAYERS.filter((l) => l.group === g.id)}
              visibility={infraVisibility}
              counts={infraCounts}
              onToggle={toggleInfra}
            />
          ))
        )}
      </div>

      <div className="mt-2 text-[10px] text-smoke-500">
        Sources: NIFC · CalOES · Open-Meteo · NASA FIRMS · OSM/OSMnx · HIFLD · NCES · FEMA
      </div>
      </div>
      )}
    </div>
  );
}

function InfraGroup({
  label,
  layers,
  visibility,
  counts,
  onToggle,
}: {
  label: string;
  layers: InfraLayer[];
  visibility: Record<string, boolean>;
  counts: Record<string, number>;
  onToggle: (id: string) => void;
}) {
  return (
    <div className="mt-1">
      <div className="text-[9px] font-semibold uppercase tracking-widest text-smoke-400">
        {label}
      </div>
      <div className="mt-0.5 space-y-0.5">
        {layers.map((l) => {
          const on = !!visibility[l.id];
          const count = counts[l.id] ?? 0;
          return (
            <label
              key={l.id}
              className="flex cursor-pointer items-center gap-2 text-[11px]"
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => onToggle(l.id)}
                className="h-3 w-3 accent-ember-500"
              />
              <span
                className="inline-block h-2 w-2 rounded-full border"
                style={{
                  backgroundColor: `${l.color}cc`,
                  borderColor: l.color,
                }}
              />
              <span className="flex-1 text-smoke-200">{l.label}</span>
              {on && (
                <span className="text-[10px] text-smoke-400">{count}</span>
              )}
            </label>
          );
        })}
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
