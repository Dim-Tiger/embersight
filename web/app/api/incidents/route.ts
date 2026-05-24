import { NextResponse } from "next/server";
import { readTestMode } from "@/lib/testModeServer";

// Test-mode incidents are personalised per cookie, so we can't share a static
// 5-minute cache across users. Mark dynamic. The upstream fetches below still
// individually cache for 5 minutes via next.revalidate, so the real-data path
// is unchanged.
export const dynamic = "force-dynamic";

// WFIGS (NIFC interagency) is the national source of truth for active US wildfires.
// It already includes CAL FIRE-managed incidents; we still hit CALFIRE separately
// because the Umbraco feed carries some CA-specific fields (cooperator counts,
// incident URLs) that aren't in WFIGS. The merge below dedupes by name + location.
const CALFIRE =
  "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false";
// IncidentTypeCategory='WF' filters out RX (prescribed burns), which are ~40%
// of the national WFIGS list and not what an IMT-facing map wants to see.
const WFIGS_POINTS =
  "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=IncidentTypeCategory%3D%27WF%27&outFields=*&f=geojson";

type Out = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  acres: number | null;
  contained_pct: number | null;
  started_at: string | null;
  source: "calfire" | "wfigs" | "synthetic";
};

async function safeJson<T>(url: string, init?: RequestInit): Promise<T | null> {
  try {
    const r = await fetch(url, { ...init, next: { revalidate: 300 } });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

/** Normalize a fire name for duplicate detection: strip "fire/incident/complex", lowercase, strip punctuation. */
function normName(s: string): string {
  return s
    .toLowerCase()
    .replace(/\s+(fire|incident|complex)\s*$/i, "")
    .replace(/[^a-z0-9]/g, "")
    .trim();
}

/** True if two lat/lon pairs are within ~15 km of each other. */
function isNearby(lat1: number, lon1: number, lat2: number, lon2: number): boolean {
  return Math.abs(lat1 - lat2) < 0.14 && Math.abs(lon1 - lon2) < 0.18;
}

export async function GET() {
  const testMode = await readTestMode();

  const [calfire, wfigs] = await Promise.all([
    safeJson<any[]>(CALFIRE),
    safeJson<any>(WFIGS_POINTS),
  ]);

  const merged: Out[] = [];

  // Prepend synthetic incidents so they appear at the top of the dropdown
  // and on the map. Real fires still load underneath for context.
  if (testMode?.enabled && testMode.syntheticIncidents.length) {
    for (const s of testMode.syntheticIncidents) {
      merged.push({
        id: s.id,
        name: s.name,
        lat: s.lat,
        lon: s.lon,
        acres: s.acres,
        contained_pct: s.contained_pct,
        started_at: s.started_at,
        source: "synthetic",
      });
    }
  }

  if (Array.isArray(calfire)) {
    for (const it of calfire) {
      const lat = Number(it?.Latitude);
      const lon = Number(it?.Longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      merged.push({
        id: `calfire:${it?.UniqueId ?? it?.Id ?? `${lat},${lon}`}`,
        name: it?.Name ?? "Unnamed Incident",
        lat,
        lon,
        acres: typeof it?.AcresBurned === "number" ? it.AcresBurned : null,
        contained_pct:
          typeof it?.PercentContained === "number"
            ? it.PercentContained / 100
            : null,
        started_at: it?.Started ?? null,
        source: "calfire",
      });
    }
  }

  if (wfigs?.features) {
    for (const f of wfigs.features) {
      const [lon, lat] = f?.geometry?.coordinates ?? [];
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const p = f?.properties ?? {};
      const name: string = p?.IncidentName ?? "Unnamed Incident";
      const id = `wfigs:${p?.IrwinID ?? p?.UniqueFireIdentifier ?? `${lat},${lon}`}`;

      // Skip if already represented by a CAL FIRE entry (same name or nearby location)
      const n = normName(name);
      if (merged.some((m) => normName(m.name) === n || isNearby(lat, lon, m.lat, m.lon))) continue;
      // Also skip exact ID duplicate
      if (merged.some((m) => m.id === id)) continue;

      // Acres fallback chain: DailyAcres is only populated on IMT-managed
      // incidents (most large CA fires). Smaller/newer fires elsewhere carry
      // size in IncidentSize or DiscoveryAcres. Take the first that's a
      // positive number so initial-attack fires don't render as 0 ac.
      const acresRaw =
        [p?.DailyAcres, p?.IncidentSize, p?.DiscoveryAcres].find(
          (v) => typeof v === "number" && v > 0,
        ) ?? null;
      merged.push({
        id,
        name,
        lat,
        lon,
        acres: typeof acresRaw === "number" ? acresRaw : null,
        contained_pct:
          typeof p?.PercentContained === "number"
            ? p.PercentContained / 100
            : null,
        started_at: p?.FireDiscoveryDateTime
            ? typeof p.FireDiscoveryDateTime === "number"
              ? new Date(p.FireDiscoveryDateTime).toISOString()
              : String(p.FireDiscoveryDateTime)
            : null,
        source: "wfigs",
      });
    }
  }

  return NextResponse.json(merged);
}
