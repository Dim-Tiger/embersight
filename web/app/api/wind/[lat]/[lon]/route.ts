import { NextResponse } from "next/server";

// Sample a square grid of wind vectors around (lat, lon) from Open-Meteo
// and return them in a shape ready for IDW interpolation client-side by
// maplibre-gl-wind's generateWindTexture().
//
// Open-Meteo supports comma-separated coordinate lists, so the whole grid
// resolves in a single round trip. No API key.

const GRID_SIZE = 7; // 7x7 = 49 points
const HALF_DEG = 0.6; // ~66km half-extent at ~38°N — covers fire vicinity

export const revalidate = 300;

type Vector = { lat: number; lon: number; speed: number; direction: number };

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ lat: string; lon: string }> },
) {
  const { lat, lon } = await ctx.params;
  const cLat = Number(lat);
  const cLon = Number(lon);
  if (!Number.isFinite(cLat) || !Number.isFinite(cLon)) {
    return NextResponse.json({ error: "bad coords" }, { status: 400 });
  }

  const lats: number[] = [];
  const lons: number[] = [];
  const step = (HALF_DEG * 2) / (GRID_SIZE - 1);
  for (let i = 0; i < GRID_SIZE; i++) {
    for (let j = 0; j < GRID_SIZE; j++) {
      lats.push(+(cLat - HALF_DEG + i * step).toFixed(4));
      lons.push(+(cLon - HALF_DEG + j * step).toFixed(4));
    }
  }

  const url =
    "https://api.open-meteo.com/v1/forecast" +
    `?latitude=${lats.join(",")}` +
    `&longitude=${lons.join(",")}` +
    "&current=wind_speed_10m,wind_direction_10m,wind_gusts_10m" +
    "&wind_speed_unit=ms";

  const r = await fetch(url, { next: { revalidate: 300 } });
  if (!r.ok) {
    return NextResponse.json(
      { error: `open-meteo ${r.status}` },
      { status: 502 },
    );
  }
  // Open-Meteo returns an array when multiple coords are supplied.
  const data = (await r.json()) as
    | Array<{
        latitude: number;
        longitude: number;
        current?: {
          wind_speed_10m?: number;
          wind_direction_10m?: number;
          wind_gusts_10m?: number;
        };
      }>
    | {
        latitude: number;
        longitude: number;
        current?: {
          wind_speed_10m?: number;
          wind_direction_10m?: number;
          wind_gusts_10m?: number;
        };
      };

  const arr = Array.isArray(data) ? data : [data];
  const vectors: Vector[] = [];
  let gustMax = 0;
  for (const pt of arr) {
    const s = pt.current?.wind_speed_10m;
    const d = pt.current?.wind_direction_10m;
    const g = pt.current?.wind_gusts_10m;
    if (typeof s !== "number" || typeof d !== "number") continue;
    vectors.push({
      lat: pt.latitude,
      lon: pt.longitude,
      speed: s,
      direction: d,
    });
    if (typeof g === "number" && g > gustMax) gustMax = g;
  }

  const west = cLon - HALF_DEG;
  const east = cLon + HALF_DEG;
  const south = cLat - HALF_DEG;
  const north = cLat + HALF_DEG;

  return NextResponse.json({
    vectors,
    bounds: [west, south, east, north],
    center: { lat: cLat, lon: cLon },
    gust_max_ms: gustMax || null,
    sampled_at: new Date().toISOString(),
  });
}
