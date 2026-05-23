import { NextResponse } from "next/server";

const UA =
  process.env.NWS_USER_AGENT ?? "EmberSight Hackathon (contact@example.com)";

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ lat: string; lon: string }> },
) {
  const { lat, lon } = await ctx.params;
  const url = `https://api.weather.gov/alerts/active?point=${lat},${lon}`;
  const r = await fetch(url, {
    headers: { "User-Agent": UA, Accept: "application/geo+json" },
    next: { revalidate: 300 },
  });
  if (!r.ok) {
    return NextResponse.json(
      { error: `nws ${r.status}` },
      { status: r.status },
    );
  }
  return NextResponse.json(await r.json());
}
