import { NextResponse } from "next/server";
import { buildSyntheticAlerts, readTestMode } from "@/lib/testModeServer";

const UA =
  process.env.NWS_USER_AGENT ?? "EmberSight Hackathon (contact@example.com)";

// Cookie-personalised → can't share a static cache. Real-data branch still
// reuses next.revalidate underneath.
export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ lat: string; lon: string }> },
) {
  const { lat, lon } = await ctx.params;

  const testMode = await readTestMode();
  if (testMode?.enabled) {
    return NextResponse.json(buildSyntheticAlerts(testMode.alertPreset));
  }

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
