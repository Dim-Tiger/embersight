"use client";

import {
  AGENT_LABELS,
  AGENT_ORDER,
  type AgentOutput,
  useStore,
} from "@/lib/store";

const AGENT_SET = new Set<string>(AGENT_ORDER);

type StoreCtx = {
  threadId: string;
};

/**
 * Consume a Server-Sent Events stream from the agent service and dispatch
 * everything into the Zustand store. Reused by both the initial agent
 * start and the resume continuation.
 */
export async function consumeAgentSse(
  body: ReadableStream<Uint8Array>,
  threadId: string,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  useStore.getState().setConnectionStatus("consuming");

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        useStore.getState().setConnectionStatus("closed");
        break;
      }
      useStore.getState().incChunk();
      buffer += decoder.decode(value, { stream: true });

      // Match \n\n (LF) or \r\n\r\n (CRLF). Some intermediaries (Next.js dev
      // server proxying through Node fetch) normalize line endings.
      const boundary = /\r?\n\r?\n/;
      let m: RegExpExecArray | null;
      while ((m = boundary.exec(buffer)) !== null) {
        const frame = buffer.slice(0, m.index);
        buffer = buffer.slice(m.index + m[0].length);
        useStore.getState().incFrame();
        handleFrame(frame, { threadId });
      }
    }
    if (buffer.trim()) handleFrame(buffer, { threadId });
  } catch (err) {
    // AbortError (DOMException) fires when the caller aborts the request
    // intentionally (e.g. starting a new run). Browsers may also surface an
    // abort as "TypeError: network error" / "TypeError: Failed to fetch" when
    // the stream is already being read. Both are expected — don't log or set
    // an error state for them; just let the caller decide.
    if (isAbortLike(err)) {
      useStore.getState().setConnectionStatus("closed");
      throw err; // re-throw so useAgentStream can skip its own error handler
    }
    console.warn("[sse] consumer threw", err);
    useStore
      .getState()
      .setError(
        `SSE stream error: ${err instanceof Error ? err.message : String(err)}`,
      );
    throw err;
  }
}

function handleFrame(frame: string, c: StoreCtx) {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return;

  let parsed: unknown = dataLines.join("\n");
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    /* keep as string */
  }

  const s = useStore.getState();

  if (event === "start") return;
  if (event === "done") {
    s.setDone(true);
    return;
  }
  if (event === "error") {
    const msg =
      parsed && typeof parsed === "object"
        ? ((parsed as Record<string, unknown>).message as string) ||
          JSON.stringify(parsed)
        : String(parsed);
    s.setError(msg);
    return;
  }
  if (event === "interrupt_pending" && parsed && typeof parsed === "object") {
    const p = parsed as { thread_id?: string; interrupt?: any };
    if (p.interrupt) {
      s.upsertInterrupt({
        thread_id: p.thread_id ?? c.threadId,
        interrupt: p.interrupt,
      });
    }
    return;
  }

  if (event === "agent-event" && parsed && typeof parsed === "object") {
    const inner = parsed as Record<string, unknown>;
    const kind = inner.kind as string | undefined;
    const name = inner.name as string | undefined;

    s.appendEvent({
      ts: Date.now(),
      kind: event,
      name: name ?? null,
      data: parsed,
    });

    if (!name) return;
    if (kind === "on_chain_start" && AGENT_SET.has(name)) {
      s.setAgentStatus(name, "running");
    } else if (kind === "on_chain_end" && AGENT_SET.has(name)) {
      const data = inner.data as Record<string, unknown> | undefined;
      const output = data?.output as Record<string, unknown> | undefined;
      const outputs = output?.outputs as
        | Record<string, AgentOutput>
        | undefined;
      const ao = outputs?.[name];
      if (ao && typeof ao === "object") {
        s.setAgentOutput(name, ao);
        if (ao.narrative) {
          s.appendChat({
            id: `a-${name}-${Date.now()}`,
            role: "agent",
            agentName: AGENT_LABELS[name] ?? name,
            text: stripPrefix(ao.narrative),
            ts: Date.now(),
          });
        }
      } else {
        s.setAgentStatus(name, "done");
      }
    }
  }
}

function stripPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
}

/**
 * Returns true for errors that represent an intentional abort or a network
 * closure that results from aborting (browsers are inconsistent about which
 * error type they throw when the stream is cut mid-read).
 */
function isAbortLike(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (
    err instanceof TypeError &&
    /network error|Failed to fetch|BodyStreamBuffer was aborted/i.test(
      err.message,
    )
  )
    return true;
  return false;
}
