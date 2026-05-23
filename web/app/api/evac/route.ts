import { NextResponse } from "next/server";

const URL_EVAC =
  "https://services.arcgis.com/BLN4oKB0N1YSgvY8/arcgis/rest/services/CA_EVACUATIONS_CalOESHosted_view/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson";

export const revalidate = 300;

export async function GET() {
  const r = await fetch(URL_EVAC, { next: { revalidate: 300 } });
  if (!r.ok) {
    return NextResponse.json(
      { error: `evac ${r.status}` },
      { status: r.status },
    );
  }
  return NextResponse.json(await r.json());
}
