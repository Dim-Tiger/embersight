import { NextResponse } from "next/server";

export const revalidate = 600;

const BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv";
// California bbox: west,south,east,north
const CA_BBOX = "-124,32,-114,42";
const SOURCE = "VIIRS_NOAA20_NRT";

type FirmsFeature = GeoJSON.Feature<
  GeoJSON.Point,
  {
    bright_ti4: number | null;
    bright_ti5: number | null;
    frp: number | null;
    confidence: string | null;
    acq_datetime: string | null;
    daynight: string | null;
  }
>;

function parseCsv(text: string): FirmsFeature[] {
  const lines = text.trim().split(/\r?\n/);
  if (lines.length < 2) return [];
  const header = lines[0].split(",").map((h) => h.trim());
  const idx = (k: string) => header.indexOf(k);
  const iLat = idx("latitude");
  const iLon = idx("longitude");
  const iBT4 = idx("bright_ti4");
  const iBT5 = idx("bright_ti5");
  const iFrp = idx("frp");
  const iConf = idx("confidence");
  const iDate = idx("acq_date");
  const iTime = idx("acq_time");
  const iDN = idx("daynight");

  if (iLat < 0 || iLon < 0) return [];

  const out: FirmsFeature[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(",");
    const lat = Number(cols[iLat]);
    const lon = Number(cols[iLon]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;

    // acq_time is HHMM in UTC (e.g. "0942")
    let acq_datetime: string | null = null;
    if (iDate >= 0 && iTime >= 0) {
      const d = cols[iDate];
      const t = cols[iTime].padStart(4, "0");
      acq_datetime = `${d}T${t.slice(0, 2)}:${t.slice(2)}:00Z`;
    }

    out.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [lon, lat] },
      properties: {
        bright_ti4: iBT4 >= 0 ? Number(cols[iBT4]) || null : null,
        bright_ti5: iBT5 >= 0 ? Number(cols[iBT5]) || null : null,
        frp: iFrp >= 0 ? Number(cols[iFrp]) || null : null,
        confidence: iConf >= 0 ? cols[iConf] : null,
        acq_datetime,
        daynight: iDN >= 0 ? cols[iDN] : null,
      },
    });
  }
  return out;
}

export async function GET(req: Request) {
  const key = process.env.FIRMS_MAP_KEY;
  if (!key) {
    return NextResponse.json(
      { error: "FIRMS_MAP_KEY not set" },
      { status: 503 },
    );
  }

  const { searchParams } = new URL(req.url);
  const daysRaw = Number(searchParams.get("days") ?? "1");
  const days = Math.min(Math.max(Number.isFinite(daysRaw) ? daysRaw : 1, 1), 10);

  const url = `${BASE}/${key}/${SOURCE}/${CA_BBOX}/${days}`;
  try {
    const r = await fetch(url, { next: { revalidate: 600 } });
    if (!r.ok) {
      return NextResponse.json(
        { error: `firms ${r.status}` },
        { status: 502 },
      );
    }
    const text = await r.text();
    const features = parseCsv(text);
    const fc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features,
    };
    return NextResponse.json(fc);
  } catch (err) {
    return NextResponse.json(
      { error: String(err) },
      { status: 502 },
    );
  }
}
