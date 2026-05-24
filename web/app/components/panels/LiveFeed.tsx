"use client";

import { AGENT_LABELS, useStore } from "@/lib/store";
import { useMemo } from "react";

export function LiveFeed() {
  const events = useStore((s) => s.agentEvents);

  const tail = useMemo(() => {
    const out: { ts: number; label: string }[] = [];
    for (let i = events.length - 1; i >= 0 && out.length < 50; i--) {
      const e = events[i];
      const inner = e.data as Record<string, unknown> | undefined;
      const kind = (inner?.kind as string) ?? "";
      const name = (inner?.name as string) ?? "";
      if (kind === "on_chain_start" && AGENT_LABELS[name]) {
        out.push({ ts: e.ts, label: `▶ ${AGENT_LABELS[name]} started` });
      } else if (kind === "on_chain_end" && AGENT_LABELS[name]) {
        out.push({ ts: e.ts, label: `✓ ${AGENT_LABELS[name]} finished` });
      } else if (kind === "on_chat_model_stream") {
        out.push({ ts: e.ts, label: `· LLM token (${name || "model"})` });
      }
    }
    return out.reverse();
  }, [events]);

  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-1.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-widest text-smoke-300">
          Live stream
        </h2>
        <span className="text-[9px] text-smoke-500">{tail.length} events</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-1">
        {tail.length === 0 ? (
          <div className="py-2 text-[10px] italic text-smoke-600">
            Waiting for events…
          </div>
        ) : (
          <ul className="space-y-0.5 font-mono text-[10px] text-smoke-400">
            {tail.map((t, i) => (
              <li key={i} className="truncate">
                <span className="text-smoke-600">
                  {new Date(t.ts).toLocaleTimeString().slice(0, 8)}
                </span>{" "}
                {t.label}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
