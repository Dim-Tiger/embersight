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
  // Chat-mode-only: track the current streaming AI message so chat_token
  // deltas and tool calls attach to it. Each chat turn creates one bubble.
  currentChatId: string | null;
};

/**
 * Consume a Server-Sent Events stream from the agent service and dispatch
 * everything into the Zustand store. Used by both briefing start and chat
 * resume; the event kinds we handle cover both modes.
 */
export async function consumeAgentSse(
  body: ReadableStream<Uint8Array>,
  threadId: string,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const ctx: StoreCtx = { threadId, currentChatId: null };
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

      const boundary = /\r?\n\r?\n/;
      let m: RegExpExecArray | null;
      while ((m = boundary.exec(buffer)) !== null) {
        const frame = buffer.slice(0, m.index);
        buffer = buffer.slice(m.index + m[0].length);
        useStore.getState().incFrame();
        handleFrame(frame, ctx);
      }
    }
    if (buffer.trim()) handleFrame(buffer, ctx);
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
  } finally {
    // If a streaming chat bubble was still open when the stream ended,
    // close it so the UI stops showing the typing indicator.
    if (ctx.currentChatId) {
      useStore.getState().finalizeAgentChat(ctx.currentChatId);
      ctx.currentChatId = null;
    }
  }
}

function handleFrame(frame: string, ctx: StoreCtx) {
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
  const p = (parsed && typeof parsed === "object" ? parsed : {}) as Record<
    string,
    unknown
  >;

  switch (event) {
    case "start": {
      const mode = (p.mode as "briefing" | "chat") ?? null;
      if (mode) s.setCurrentMode(mode);
      return;
    }

    case "done": {
      const mode = (p.mode as "briefing" | "chat") ?? null;
      if (mode === "briefing") s.setBriefingComplete(true);
      if (ctx.currentChatId) {
        s.finalizeAgentChat(ctx.currentChatId);
        ctx.currentChatId = null;
      }
      s.setDone(true);
      return;
    }

    case "error": {
      const msg =
        typeof p.message === "string" ? p.message : JSON.stringify(parsed);
      s.setError(msg);
      return;
    }

    case "interrupt_pending": {
      const i = p.interrupt as Record<string, unknown> | undefined;
      if (i) {
        s.upsertInterrupt({
          thread_id: (p.thread_id as string) ?? ctx.threadId,
          interrupt: i as any,
        });
      }
      return;
    }

    case "tool_call_start": {
      const name = p.name as string | undefined;
      const runId = (p.run_id as string) ?? `${Date.now()}`;
      if (!name) return;
      // Lazy-create the chat bubble if the IC kicked off a tool before
      // emitting any tokens.
      if (!ctx.currentChatId) {
        ctx.currentChatId = `ai-${Date.now()}`;
        s.beginAgentChat(ctx.currentChatId);
      }
      const agentName = name.replace(/^consult_/, "");
      s.attachToolCall(ctx.currentChatId, {
        id: runId,
        name,
        agentLabel: AGENT_LABELS[agentName] ?? agentName,
        args: (p.args as Record<string, unknown>) ?? {},
        status: "running",
        ts: Date.now(),
      });
      s.setAgentStatus(agentName, "running");
      return;
    }

    case "tool_call_end": {
      const name = p.name as string | undefined;
      const runId = (p.run_id as string) ?? "";
      if (!name) return;
      const summary = (p.summary as Record<string, unknown>) ?? {};
      // Tool summary often contains the agent narrative + headlines.
      // Patch the outputs map opportunistically so the reference tabs
      // refresh too. The shape mirrors what _summarize() emits in
      // agents/tools.py.
      const agentName = name.replace(/^consult_/, "");
      if (ctx.currentChatId && runId) {
        s.resolveToolCall(ctx.currentChatId, runId, summary);
      }
      if (summary && typeof summary === "object" && summary.narrative) {
        // Synthesize a partial AgentOutput so the cards in tabs update.
        const existing = useStore.getState().agentOutputs[agentName];
        const merged: AgentOutput = {
          agent: agentName,
          narrative: String(summary.narrative ?? existing?.narrative ?? ""),
          payload: {
            ...(existing?.payload ?? {}),
            ...(typeof summary.headlines === "object" && summary.headlines
              ? (summary.headlines as Record<string, unknown>)
              : {}),
          },
          confidence:
            typeof summary.confidence === "number"
              ? (summary.confidence as number)
              : (existing?.confidence ?? 0.5),
          confidence_driver:
            (summary.confidence_driver as string) ??
            existing?.confidence_driver,
          citation_bundle: existing?.citation_bundle,
        };
        s.setAgentOutput(agentName, merged);
      } else {
        s.setAgentStatus(agentName, "done");
      }
      return;
    }

    case "chat_token": {
      const delta = (p.delta as string) ?? "";
      if (!delta) return;
      if (!ctx.currentChatId) {
        ctx.currentChatId = `ai-${Date.now()}`;
        s.beginAgentChat(ctx.currentChatId);
      }
      s.appendChatToken(ctx.currentChatId, delta);
      return;
    }

    case "agent-event": {
      const kind = p.kind as string | undefined;
      const name = p.name as string | undefined;

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
        const data = p.data as Record<string, unknown> | undefined;
        const output = data?.output as Record<string, unknown> | undefined;
        const outputs = output?.outputs as
          | Record<string, AgentOutput>
          | undefined;
        const ao = outputs?.[name];
        if (ao && typeof ao === "object") {
          s.setAgentOutput(name, ao);
          // Briefing mode: surface each subagent's narrative as a system
          // ticker entry so the human watches the team work. Chat mode
          // skips this because only the IC's voice should appear.
          if (s.currentMode !== "chat" && ao.narrative) {
            s.appendChat({
              id: `a-${name}-${Date.now()}`,
              role: "system",
              agentName: AGENT_LABELS[name] ?? name,
              text: stripPrefix(ao.narrative).slice(0, 280),
              ts: Date.now(),
            });
          }
        } else {
          s.setAgentStatus(name, "done");
        }
      }
      return;
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
