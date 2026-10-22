"use client";

import { ArrowRight, Clock } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { SEVERITY, SEVERITY_RANK, type Severity, SeverityBadge } from "./Severity";

export type AttentionItem = {
  id: string;
  level: Severity;
  /** The big number or short fact, e.g. "1,240" or "TP-3". */
  value?: string;
  /** Unit printed right after the value, e.g. "people" or "h". */
  unit?: string;
  /** One-line statement of what it means. */
  title: string;
  /** Optional second line: source, threshold, or what to do. */
  detail?: ReactNode;
  /** Optional call to action (e.g. jump to the approval queue). */
  action?: { label: string; onClick: () => void };
};

/**
 * "What needs attention now": the first thing on a triage tab. Shows at
 * most `max` items, most severe first, as big-number cards so the IC can
 * take in the picture in about five seconds before reading anything else.
 */
export function AttentionStrip({
  items,
  asOf,
  emptyText,
  max = 3,
}: {
  items: AttentionItem[];
  asOf?: ReactNode;
  emptyText?: string;
  max?: number;
}) {
  const sorted = [...items]
    .sort((a, b) => SEVERITY_RANK[a.level] - SEVERITY_RANK[b.level])
    .slice(0, max);
  const hidden = items.length - sorted.length;
  return (
    <section aria-label="Needs attention now">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-[12px] font-bold uppercase tracking-widest text-smoke-200">
          Needs attention now
        </h3>
        {asOf}
      </div>
      {sorted.length === 0 ? (
        <p className="rounded-md border border-smoke-700 bg-smoke-800/60 px-4 py-3 text-[13px] text-smoke-300">
          {emptyText ?? "Nothing flagged yet."}
        </p>
      ) : (
        <ol
          className={`grid gap-2 ${
            sorted.length === 1
              ? "grid-cols-1"
              : sorted.length === 2
                ? "grid-cols-1 md:grid-cols-2"
                : "grid-cols-1 md:grid-cols-3"
          }`}
        >
          {sorted.map((it) => (
            <AttentionCard key={it.id} item={it} />
          ))}
        </ol>
      )}
      {hidden > 0 && (
        <p className="mt-1.5 text-[11px] text-smoke-400">
          +{hidden} more flagged below.
        </p>
      )}
    </section>
  );
}

function AttentionCard({ item }: { item: AttentionItem }) {
  const s = SEVERITY[item.level];
  return (
    <li
      className={`flex min-w-0 flex-col gap-1.5 rounded-md border border-l-4 border-smoke-700 bg-smoke-800 px-3.5 py-3 ${s.edge}`}
    >
      <SeverityBadge level={item.level} />
      {item.value != null && (
        <div className="flex items-baseline gap-1.5">
          <span className="text-[28px] font-bold leading-none tabular-nums text-smoke-100">
            {item.value}
          </span>
          {item.unit && (
            <span className="text-[13px] font-semibold text-smoke-300">
              {item.unit}
            </span>
          )}
        </div>
      )}
      <p className="text-[13.5px] font-semibold leading-snug text-smoke-100">
        {item.title}
      </p>
      {item.detail && (
        <div className="text-[12px] leading-snug text-smoke-300">
          {item.detail}
        </div>
      )}
      {item.action && (
        <button
          onClick={item.action.onClick}
          className="mt-auto inline-flex w-fit items-center gap-1 rounded border border-smoke-600 bg-smoke-700 px-2.5 py-1 text-[12px] font-semibold text-smoke-100 hover:border-ember-500 hover:bg-ember-900/40"
        >
          {item.action.label}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      )}
    </li>
  );
}

/** "As of 14:32 (3 min ago)". Shown next to every triage header. */
export function AsOf({ ts, label = "As of" }: { ts: number | null; label?: string }) {
  // Re-render every 30 s so "n min ago" never goes stale on a quiet tab:
  // an old report that still says "just now" is worse than no timestamp.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);
  if (ts == null) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-smoke-400">
        <Clock className="h-3 w-3" aria-hidden />
        No report yet
      </span>
    );
  }
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60000));
  const time = new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  return (
    <span
      className="inline-flex items-center gap-1 text-[11px] text-smoke-300"
      title={new Date(ts).toString()}
    >
      <Clock className="h-3 w-3" aria-hidden />
      {label} {time}
      <span className="text-smoke-400">
        ({mins === 0 ? "just now" : `${mins} min ago`})
      </span>
    </span>
  );
}
