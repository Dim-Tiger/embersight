// Proxy SSE from the FastAPI agent service to the browser.
// We POST the start payload and pipe the upstream response body straight through.

const AGENT_BASE =
  process.env.AGENT_BASE_URL ?? "http://localhost:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.text();

  let upstream: Response;
  try {
    upstream = await fetch(`${AGENT_BASE}/agent/stream`, {
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
    // Pass the upstream body through so the client can see the Pydantic
    // validation error (422) or any other structured error from FastAPI.
    const errBody = await upstream.text().catch(() => upstream.statusText);
    console.error(`[agent/stream] upstream ${upstream.status}:`, errBody);
    return new Response(errBody, {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
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
