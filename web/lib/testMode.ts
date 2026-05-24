"use client";

import { create } from "zustand";

/**
 * Client-side test-mode harness. When enabled, queries.ts hooks merge or
 * substitute synthetic data so you can drive the UI (and, via the start
 * payload, the agent) without depending on live CAL FIRE / WFIGS / NWS feeds.
 *
 * Test mode lives entirely in the browser (localStorage). Server API routes
 * are untouched — overrides apply at the React-Query layer.
 */

export type SyntheticIncident = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  acres: number;
  contained_pct: number;
  started_at: string;
};

export type WindOverride = {
  /** wind FROM direction in degrees (meteorological convention: 0=N, 90=E) */
  direction_deg: number;
  /** sustained wind speed, m/s */
  speed_ms: number;
  /** peak gust, m/s — null hides gust readout */
  gusts_ms: number | null;
};

export type WeatherAlertPreset =
  | "none"
  | "red_flag_warning"
  | "fire_weather_watch"
  | "high_wind_warning"
  | "excessive_heat_warning";

export const WEATHER_ALERT_LABELS: Record<WeatherAlertPreset, string> = {
  none: "No alerts",
  red_flag_warning: "Red Flag Warning",
  fire_weather_watch: "Fire Weather Watch",
  high_wind_warning: "High Wind Warning",
  excessive_heat_warning: "Excessive Heat Warning",
};

export type TestModeStore = {
  enabled: boolean;
  hydrated: boolean;
  syntheticIncidents: SyntheticIncident[];
  /** When true, clicks on the map drop a new synthetic ignition at the click. */
  placementMode: boolean;
  /** Default name/acres applied to the next placement. */
  draftName: string;
  draftAcres: number;
  wind: WindOverride;
  alertPreset: WeatherAlertPreset;

  hydrate: () => void;
  setEnabled: (b: boolean) => void;
  setPlacementMode: (b: boolean) => void;
  setDraftName: (s: string) => void;
  setDraftAcres: (n: number) => void;
  addIncident: (lat: number, lon: number) => SyntheticIncident;
  removeIncident: (id: string) => void;
  clearIncidents: () => void;
  setWind: (patch: Partial<WindOverride>) => void;
  setAlertPreset: (p: WeatherAlertPreset) => void;
  reset: () => void;
};

const STORAGE_KEY = "embersight-test-mode";

const DEFAULT_WIND: WindOverride = {
  direction_deg: 45, // NE — drives toward SW (downslope, classic CA pattern)
  speed_ms: 8,
  gusts_ms: 14,
};

const DEFAULTS = {
  enabled: false,
  syntheticIncidents: [] as SyntheticIncident[],
  placementMode: false,
  draftName: "Test Ridge Fire",
  draftAcres: 250,
  wind: DEFAULT_WIND,
  alertPreset: "none" as WeatherAlertPreset,
};

type Persisted = {
  enabled: boolean;
  syntheticIncidents: SyntheticIncident[];
  draftName: string;
  draftAcres: number;
  wind: WindOverride;
  alertPreset: WeatherAlertPreset;
};

function readInitial(): Persisted {
  if (typeof window === "undefined") {
    return {
      enabled: DEFAULTS.enabled,
      syntheticIncidents: DEFAULTS.syntheticIncidents,
      draftName: DEFAULTS.draftName,
      draftAcres: DEFAULTS.draftAcres,
      wind: DEFAULTS.wind,
      alertPreset: DEFAULTS.alertPreset,
    };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) throw new Error("empty");
    const parsed = JSON.parse(raw) as Partial<Persisted>;
    return {
      enabled: !!parsed.enabled,
      syntheticIncidents: Array.isArray(parsed.syntheticIncidents)
        ? parsed.syntheticIncidents
        : [],
      draftName:
        typeof parsed.draftName === "string"
          ? parsed.draftName
          : DEFAULTS.draftName,
      draftAcres:
        typeof parsed.draftAcres === "number"
          ? parsed.draftAcres
          : DEFAULTS.draftAcres,
      wind: { ...DEFAULTS.wind, ...(parsed.wind ?? {}) },
      alertPreset: (parsed.alertPreset ?? DEFAULTS.alertPreset) as WeatherAlertPreset,
    };
  } catch {
    return {
      enabled: DEFAULTS.enabled,
      syntheticIncidents: DEFAULTS.syntheticIncidents,
      draftName: DEFAULTS.draftName,
      draftAcres: DEFAULTS.draftAcres,
      wind: DEFAULTS.wind,
      alertPreset: DEFAULTS.alertPreset,
    };
  }
}

function persist(state: TestModeStore) {
  if (typeof window === "undefined") return;
  const snapshot: Persisted = {
    enabled: state.enabled,
    syntheticIncidents: state.syntheticIncidents,
    draftName: state.draftName,
    draftAcres: state.draftAcres,
    wind: state.wind,
    alertPreset: state.alertPreset,
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // localStorage full / disabled — fail quiet, in-memory state still works.
  }
}

export const useTestMode = create<TestModeStore>((set, get) => ({
  enabled: DEFAULTS.enabled,
  hydrated: false,
  syntheticIncidents: DEFAULTS.syntheticIncidents,
  placementMode: DEFAULTS.placementMode,
  draftName: DEFAULTS.draftName,
  draftAcres: DEFAULTS.draftAcres,
  wind: DEFAULTS.wind,
  alertPreset: DEFAULTS.alertPreset,

  hydrate: () => {
    if (get().hydrated) return;
    const initial = readInitial();
    set({ ...initial, hydrated: true });
  },

  setEnabled: (b) => {
    set({ enabled: b, placementMode: b ? get().placementMode : false });
    persist(get());
  },

  setPlacementMode: (b) => {
    set({ placementMode: b });
  },

  setDraftName: (s) => {
    set({ draftName: s });
    persist(get());
  },

  setDraftAcres: (n) => {
    set({ draftAcres: n });
    persist(get());
  },

  addIncident: (lat, lon) => {
    const id = `synthetic:${Date.now().toString(36)}`;
    const name = get().draftName.trim() || "Test Fire";
    const inc: SyntheticIncident = {
      id,
      name,
      lat,
      lon,
      acres: Math.max(0, get().draftAcres),
      contained_pct: 0,
      started_at: new Date().toISOString(),
    };
    set((s) => ({ syntheticIncidents: [...s.syntheticIncidents, inc] }));
    persist(get());
    return inc;
  },

  removeIncident: (id) => {
    set((s) => ({
      syntheticIncidents: s.syntheticIncidents.filter((i) => i.id !== id),
    }));
    persist(get());
  },

  clearIncidents: () => {
    set({ syntheticIncidents: [] });
    persist(get());
  },

  setWind: (patch) => {
    set((s) => ({ wind: { ...s.wind, ...patch } }));
    persist(get());
  },

  setAlertPreset: (p) => {
    set({ alertPreset: p });
    persist(get());
  },

  reset: () => {
    set({
      enabled: false,
      placementMode: false,
      syntheticIncidents: [],
      draftName: DEFAULTS.draftName,
      draftAcres: DEFAULTS.draftAcres,
      wind: DEFAULTS.wind,
      alertPreset: DEFAULTS.alertPreset,
    });
    persist(get());
  },
}));

/**
 * Build a synthetic NWS-alerts-style payload for a weather preset.
 * Matches api.weather.gov GeoJSON shape so WeatherTab consumers don't have to
 * branch on shape.
 */
export function buildSyntheticAlerts(
  preset: WeatherAlertPreset,
): GeoJSON.FeatureCollection {
  const now = new Date();
  const ends = new Date(now.getTime() + 12 * 3600_000);
  const base = {
    type: "FeatureCollection" as const,
    features: [] as GeoJSON.Feature[],
  };
  if (preset === "none") return base;

  const presets: Record<
    Exclude<WeatherAlertPreset, "none">,
    { event: string; headline: string; severity: string; description: string }
  > = {
    red_flag_warning: {
      event: "Red Flag Warning",
      headline: "Red Flag Warning in effect — critical fire weather",
      severity: "Severe",
      description:
        "[TEST] Critical fire weather conditions: low humidity, sustained winds, dry fuels.",
    },
    fire_weather_watch: {
      event: "Fire Weather Watch",
      headline: "Fire Weather Watch — elevated ignition risk",
      severity: "Moderate",
      description:
        "[TEST] Conditions favorable for the rapid spread of wildfires expected.",
    },
    high_wind_warning: {
      event: "High Wind Warning",
      headline: "High Wind Warning — sustained winds 40+ mph",
      severity: "Severe",
      description:
        "[TEST] Damaging winds will blow down trees and power lines; widespread power outages possible.",
    },
    excessive_heat_warning: {
      event: "Excessive Heat Warning",
      headline: "Excessive Heat Warning — dangerous heat",
      severity: "Severe",
      description:
        "[TEST] Dangerously hot conditions with afternoon highs near 110°F.",
    },
  };
  const p = presets[preset];
  base.features.push({
    type: "Feature",
    geometry: null as unknown as GeoJSON.Geometry,
    properties: {
      "@id": `synthetic-alert-${preset}`,
      id: `synthetic-alert-${preset}`,
      event: p.event,
      headline: p.headline,
      severity: p.severity,
      certainty: "Likely",
      urgency: "Expected",
      description: p.description,
      effective: now.toISOString(),
      onset: now.toISOString(),
      expires: ends.toISOString(),
      ends: ends.toISOString(),
      sender: "EmberSight Test Mode",
      senderName: "EmberSight Test Mode",
    },
  });
  return base;
}

/**
 * Build a uniform wind grid that matches the /api/wind shape so IncidentMap's
 * particle layer can render it without any branching.
 */
export function buildSyntheticWindGrid(
  centerLat: number,
  centerLon: number,
  wind: WindOverride,
): {
  vectors: Array<{ lat: number; lon: number; speed: number; direction: number }>;
  bounds: [number, number, number, number];
  center: { lat: number; lon: number };
  gust_max_ms: number | null;
  sampled_at: string;
} {
  const GRID = 7;
  const HALF = 0.6;
  const step = (HALF * 2) / (GRID - 1);
  const vectors = [];
  for (let i = 0; i < GRID; i++) {
    for (let j = 0; j < GRID; j++) {
      vectors.push({
        lat: +(centerLat - HALF + i * step).toFixed(4),
        lon: +(centerLon - HALF + j * step).toFixed(4),
        speed: wind.speed_ms,
        direction: wind.direction_deg,
      });
    }
  }
  return {
    vectors,
    bounds: [
      centerLon - HALF,
      centerLat - HALF,
      centerLon + HALF,
      centerLat + HALF,
    ],
    center: { lat: centerLat, lon: centerLon },
    gust_max_ms: wind.gusts_ms,
    sampled_at: new Date().toISOString(),
  };
}
