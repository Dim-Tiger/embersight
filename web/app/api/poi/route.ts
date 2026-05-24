/**
 * Generic critical-infrastructure POI proxy.
 *
 *   GET /api/poi?source=<id>&bbox=west,south,east,north
 *
 * Looks the source up in INFRA_SOURCES, fetches from ArcGIS or OSM
 * Overpass server-side (no browser CORS, no API keys in client),
 * normalizes the result to a GeoJSON FeatureCollection, and caches per
 * the source's revalidate.
 *
 * Errors return `{ type: "FeatureCollection", features: [] }` with a 200
 * so map layers fail soft — matching the "missing data is just no
 * pins" UX of the existing perimeter/firms routes.
 */

import { NextRequest, NextResponse } from "next/server";

import { INFRA_SOURCES, type InfraSource } from "@/lib/infraLayers";

const OVERPASS_USER_AGENT =
  "EmberSight/0.1 (+https://github.com/Dim-Tiger/embersight)";
const OVERPASS_TIMEOUT_S = 25;

type Bbox = { west: number; south: number; east: number; north: number };

function parseBbox(raw: string | null): Bbox | null {
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return null;
  const [west, south, east, north] = parts;
  if (west >= east || south >= north) return null;
  return { west, south, east, north };
}

function emptyFC(reason?: string): NextResponse {
  return NextResponse.json({
    type: "FeatureCollection" as const,
    features: [],
    ...(reason ? { error: reason } : {}),
  });
}

// --------------------------------------------------------------------------- //
// ArcGIS path
// --------------------------------------------------------------------------- //

async function fetchArcgis(
  src: InfraSource,
  bbox: Bbox,
): Promise<GeoJSON.FeatureCollection> {
  const envelope = JSON.stringify({
    xmin: bbox.west,
    ymin: bbox.south,
    xmax: bbox.east,
    ymax: bbox.north,
    spatialReference: { wkid: 4326 },
  });
  const params = new URLSearchParams({
    where: "1=1",
    geometry: envelope,
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields: src.outFields ?? "*",
    returnGeometry: "true",
    outSR: "4326",
    resultRecordCount: "1000",
    f: "geojson",
  });
  const url = `${src.endpoint}?${params.toString()}`;
  const r = await fetch(url, { next: { revalidate: src.revalidate } });
  if (!r.ok) throw new Error(`arcgis ${r.status}`);
  const data = (await r.json()) as
    | GeoJSON.FeatureCollection
    | { error: { message?: string } };
  // ArcGIS 200-with-error envelope.
  if ("error" in data) {
    throw new Error(`arcgis: ${data.error?.message ?? "unknown"}`);
  }
  return data;
}

// --------------------------------------------------------------------------- //
// Overpass path
// --------------------------------------------------------------------------- //

function buildOverpassQuery(src: InfraSource, bbox: Bbox): string {
  const { south, west, north, east } = bbox;
  const bboxClause = `(${south},${west},${north},${east})`;
  const parts: string[] = [];
  for (const filter of src.overpassFilters ?? []) {
    const valueClause =
      filter.values.length === 1
        ? `["${filter.key}"="${filter.values[0]}"]`
        : `["${filter.key}"~"^(${filter.values.join("|")})$"]`;
    for (const etype of filter.elementTypes ?? ["node", "way", "relation"]) {
      parts.push(`${etype}${valueClause}${bboxClause};`);
    }
  }
  return `[out:json][timeout:${OVERPASS_TIMEOUT_S}];\n(\n  ${parts.join("\n  ")}\n);\nout center tags;`;
}

type OverpassElement = {
  type: "node" | "way" | "relation";
  id: number;
  lat?: number;
  lon?: number;
  center?: { lat: number; lon: number };
  tags?: Record<string, string>;
};

async function fetchOverpass(
  src: InfraSource,
  bbox: Bbox,
): Promise<GeoJSON.FeatureCollection> {
  const query = buildOverpassQuery(src, bbox);
  const r = await fetch(src.endpoint, {
    method: "POST",
    headers: {
      "User-Agent": OVERPASS_USER_AGENT,
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ data: query }).toString(),
    next: { revalidate: src.revalidate },
  });
  if (!r.ok) throw new Error(`overpass ${r.status}`);
  const data = (await r.json()) as { elements?: OverpassElement[] };
  const features: GeoJSON.Feature[] = [];
  for (const el of data.elements ?? []) {
    const lat = el.lat ?? el.center?.lat;
    const lon = el.lon ?? el.center?.lon;
    if (lat == null || lon == null) continue;
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: {
        osm_id: `${el.type}/${el.id}`,
        name:
          el.tags?.name ??
          el.tags?.operator ??
          el.tags?.["addr:housename"] ??
          null,
        ...(el.tags ?? {}),
      },
    });
  }
  return { type: "FeatureCollection", features };
}

// --------------------------------------------------------------------------- //
// Handler
// --------------------------------------------------------------------------- //

export async function GET(req: NextRequest) {
  const sourceId = req.nextUrl.searchParams.get("source");
  const bboxRaw = req.nextUrl.searchParams.get("bbox");
  if (!sourceId) return emptyFC("missing source");
  const src = INFRA_SOURCES[sourceId];
  if (!src) return emptyFC(`unknown source: ${sourceId}`);
  const bbox = parseBbox(bboxRaw);
  if (!bbox) return emptyFC("missing or invalid bbox");

  try {
    const fc =
      src.kind === "arcgis"
        ? await fetchArcgis(src, bbox)
        : await fetchOverpass(src, bbox);
    return NextResponse.json(fc);
  } catch (err) {
    // Fail soft — map renders zero features, Legend shows count 0.
    return emptyFC(err instanceof Error ? err.message : String(err));
  }
}
