// Display helpers for numbers an incident commander reads at a glance.
//
// US fire operations talk in mph and compass points ("15 mph from the ESE"),
// while our wind feed (Open-Meteo) returns m/s and a FROM bearing in degrees.
// Every wind readout in the dashboard goes through these helpers so the map
// label, the legend and the tabs can never disagree about units or about
// whether a bearing means "from" or "toward".

const MPH_PER_MS = 2.23694;

export function msToMph(ms: number): number {
  return ms * MPH_PER_MS;
}

const POINTS = [
  "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
] as const;

/** 16-point compass name for a bearing in degrees (0 = north, clockwise). */
export function cardinal(deg: number): string {
  const d = ((deg % 360) + 360) % 360;
  return POINTS[Math.round(d / 22.5) % 16];
}

/**
 * "15 mph from ESE · gusts 27 mph". `fromDeg` is the meteorological
 * convention: the direction the wind blows FROM.
 */
export function formatWind(
  speedMs: number,
  fromDeg: number,
  gustMs?: number | null,
): string {
  const base = `${Math.round(msToMph(speedMs))} mph from ${cardinal(fromDeg)}`;
  if (gustMs == null || !Number.isFinite(gustMs)) return base;
  return `${base} · gusts ${Math.round(msToMph(gustMs))} mph`;
}

/**
 * "Sep 15 (37 days ago)" / "today 2:14 PM". An IC cares how long a fire has
 * been burning more than the calendar date, so the age comes first in
 * spirit and the date is kept for the log.
 */
export function formatStarted(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const ms = now.getTime() - d.getTime();
  const hours = Math.floor(ms / 3_600_000);
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (ms < 0) return `${date}, ${time}`;
  if (hours < 1) return `${time} (under 1 h ago)`;
  if (hours < 48) return `${date}, ${time} (${hours} h ago)`;
  return `${date} (${Math.floor(hours / 24)} days ago)`;
}

/** Great-circle distance in km between two lat/lon points. */
export function haversineKm(
  lat1: number,
  lon1: number,
  lat2: number,
  lon2: number,
): number {
  const R = 6371;
  const toRad = (x: number) => (x * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

export function kmToMi(km: number): number {
  return km * 0.621371;
}
