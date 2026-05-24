/**
 * Server-side reader for the embersight_test cookie.
 *
 * Used by:
 *   - /api/incidents — to merge synthetic fires into the incident list
 *   - /api/weather/[lat]/[lon] — to substitute synthetic alerts
 *   - /api/wind/[lat]/[lon] — to substitute a uniform synthetic wind grid
 *   - /api/agent/stream — to inject `test_overrides` into the body forwarded
 *                          to the Python agent
 *
 * Mirrors the Persisted shape from lib/testMode.ts. Kept as a separate
 * module so route handlers don't pull in zustand or any client-only code.
 */

import { cookies } from "next/headers";

export type ServerSyntheticIncident = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  acres: number;
  contained_pct: number;
  started_at: string;
};

export type ServerWindOverride = {
  direction_deg: number;
  speed_ms: number;
  gusts_ms: number | null;
};

export type ServerWeatherAlertPreset =
  | "none"
  | "red_flag_warning"
  | "fire_weather_watch"
  | "high_wind_warning"
  | "excessive_heat_warning";

export type ServerTestModePayload = {
  enabled: boolean;
  syntheticIncidents: ServerSyntheticIncident[];
  wind: ServerWindOverride;
  alertPreset: ServerWeatherAlertPreset;
};

export const TEST_COOKIE_NAME = "embersight_test";

const DEFAULT_WIND: ServerWindOverride = {
  direction_deg: 45,
  speed_ms: 8,
  gusts_ms: 14,
};

export async function readTestMode(): Promise<ServerTestModePayload | null> {
  let raw: string | undefined;
  try {
    const jar = await cookies();
    raw = jar.get(TEST_COOKIE_NAME)?.value;
  } catch {
    // cookies() throws outside a request scope. Treat as absent.
    return null;
  }
  if (!raw) return null;
  return parseTestModeCookie(raw);
}

/** Parsing helper exposed so route handlers reading from req.headers can use
 *  the same logic — `cookies()` is Next-only and we may also need to parse
 *  cookies from an arbitrary Request (for body-stream proxies). */
export function parseTestModeCookie(
  raw: string | null | undefined,
): ServerTestModePayload | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<ServerTestModePayload>;
    return {
      enabled: !!parsed.enabled,
      syntheticIncidents: Array.isArray(parsed.syntheticIncidents)
        ? parsed.syntheticIncidents
        : [],
      wind: { ...DEFAULT_WIND, ...(parsed.wind ?? {}) },
      alertPreset:
        (parsed.alertPreset as ServerWeatherAlertPreset) ?? "none",
    };
  } catch {
    return null;
  }
}

/** Read the test cookie directly from a Request's Cookie header. Useful in
 *  the agent/stream proxy where we want to read the cookie without depending
 *  on the Next async-cookies() scope. */
export function readTestModeFromRequest(
  req: Request,
): ServerTestModePayload | null {
  const header = req.headers.get("cookie");
  if (!header) return null;
  const target = TEST_COOKIE_NAME + "=";
  for (const raw of header.split(";")) {
    const c = raw.trim();
    if (c.startsWith(target)) {
      return parseTestModeCookie(decodeURIComponent(c.slice(target.length)));
    }
  }
  return null;
}

/** Build a synthetic NWS-alerts payload matching api.weather.gov shape. */
export function buildSyntheticAlerts(
  preset: ServerWeatherAlertPreset,
): GeoJSON.FeatureCollection {
  const now = new Date();
  const ends = new Date(now.getTime() + 12 * 3600_000);
  const base: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: [],
  };
  if (preset === "none") return base;
  const presets: Record<
    Exclude<ServerWeatherAlertPreset, "none">,
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
        "[TEST] Damaging winds will blow down trees and power lines.",
    },
    excessive_heat_warning: {
      event: "Excessive Heat Warning",
      headline: "Excessive Heat Warning — dangerous heat",
      severity: "Severe",
      description: "[TEST] Dangerously hot conditions, highs near 110°F.",
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

export function buildSyntheticWindGrid(
  centerLat: number,
  centerLon: number,
  wind: ServerWindOverride,
) {
  const GRID = 7;
  const HALF = 0.6;
  const step = (HALF * 2) / (GRID - 1);
  const vectors: Array<{
    lat: number;
    lon: number;
    speed: number;
    direction: number;
  }> = [];
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
    ] as [number, number, number, number],
    center: { lat: centerLat, lon: centerLon },
    gust_max_ms: wind.gusts_ms,
    sampled_at: new Date().toISOString(),
  };
}

/** Convert the cookie payload into the lowercase-snake_case shape the Python
 *  agent expects in its `test_overrides` field. Returns null when no override
 *  applies (saves a few bytes on the wire). */
export function toAgentOverrides(
  payload: ServerTestModePayload | null,
): {
  enabled: boolean;
  wind: ServerWindOverride;
  alert_preset: ServerWeatherAlertPreset;
} | null {
  if (!payload || !payload.enabled) return null;
  return {
    enabled: true,
    wind: payload.wind,
    alert_preset: payload.alertPreset,
  };
}
