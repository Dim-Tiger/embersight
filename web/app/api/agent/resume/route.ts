const AGENT_BASE = process.env.AGENT_BASE_URL ?? "http://localhost:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.text();
  const upstream = await fetch(`${AGENT_BASE}/agent/resume`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    cache: "no-store",
  });

  if (!upstream.ok || !upstream.body) {
    return new Response(
      `upstream agent error: ${upstream.status} ${upstream.statusText}`,
      { status: 502 },
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
