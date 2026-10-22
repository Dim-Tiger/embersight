const AGENT_BASE = process.env.AGENT_BASE_URL ?? "http://localhost:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.text();
  // Same guard as /api/agent/stream: if the agent service is down, fetch
  // throws, and without a catch Next answers an opaque 500 and logs a stack
  // trace. A 502 with a reason lets the approval card tell the IC that the
  // decision was NOT recorded.
  let upstream: Response;
  try {
    upstream = await fetch(`${AGENT_BASE}/agent/resume`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return new Response(
      JSON.stringify({ error: "agent unreachable", detail: msg }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

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
