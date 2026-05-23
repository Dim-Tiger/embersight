import { NextRequest, NextResponse } from "next/server";

export const revalidate = 300;

const WFIGS_PERIMETERS =
  "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query";

/** Loose GUID/UUID validator — only allow hex digits, hyphens, braces */
function isSafeId(s: string): boolean {
  return /^[{]?[0-9a-fA-F-]{32,40}[}]?$/.test(s);
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const lat = searchParams.get("lat");
  const lon = searchParams.get("lon");
  const rawIrwinId = searchParams.get("irwinId");

  if (!lat || !lon) {
    return NextResponse.json({ error: "lat and lon required" }, { status: 400 });
  }

  const latN = Number(lat);
  const lonN = Number(lon);
  if (!Number.isFinite(latN) || !Number.isFinite(lonN)) {
    return NextResponse.json({ error: "invalid coordinates" }, { status: 400 });
  }

  let params: URLSearchParams;

  // Prefer IrwinID lookup for WFIGS incidents (more precise)
  if (rawIrwinId && isSafeId(rawIrwinId)) {
    params = new URLSearchParams({
      where: `poly_IRWINID='${rawIrwinId}'`,
      outFields: "poly_IRWINID,poly_IncidentName,poly_GISAcres,poly_CreateDate",
      f: "geojson",
    });
  } else {
    // Fallback: spatial intersection with the incident point
    params = new URLSearchParams({
      where: "1=1",
      geometry: JSON.stringify({ x: lonN, y: latN }),
      geometryType: "esriGeometryPoint",
      spatialRel: "esriSpatialRelIntersects",
      inSR: "4326",
      outFields: "poly_IRWINID,poly_IncidentName,poly_GISAcres,poly_CreateDate",
      f: "geojson",
    });
  }

  try {
    const r = await fetch(`${WFIGS_PERIMETERS}?${params}`, {
      next: { revalidate: 300 },
    });
    if (!r.ok) return NextResponse.json(null);
    const data = await r.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(null);
  }
}

