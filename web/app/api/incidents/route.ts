import { NextResponse } from "next/server";

export const revalidate = 300;

const CALFIRE =
  "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=false";
const WFIGS_POINTS =
  "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query?where=POOState%3D%27US-CA%27&outFields=*&f=geojson";

type Out = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  acres: number | null;
  contained_pct: number | null;
  started_at: string | null;
  source: "calfire" | "wfigs";
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

export async function GET() {
  const [calfire, wfigs] = await Promise.all([
    safeJson<any[]>(CALFIRE),
    safeJson<any>(WFIGS_POINTS),
  ]);

  const merged: Out[] = [];

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
      const id = `wfigs:${p?.IrwinID ?? p?.UniqueFireIdentifier ?? `${lat},${lon}`}`;
      if (merged.some((m) => m.id === id)) continue;
      merged.push({
        id,
        name: p?.IncidentName ?? "Unnamed Incident",
        lat,
        lon,
        acres: typeof p?.DailyAcres === "number" ? p.DailyAcres : null,
        contained_pct:
          typeof p?.PercentContained === "number"
            ? p.PercentContained / 100
            : null,
        started_at: p?.FireDiscoveryDateTime ?? null,
        source: "wfigs",
      });
    }
  }

  return NextResponse.json(merged);
}
