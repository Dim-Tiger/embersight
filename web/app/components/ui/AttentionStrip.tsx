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
  /** Always shown first, whatever its severity. Used for decisions only
   * the IC can make (pending approvals), which must never be demoted to
   * the overflow line by a higher-severity fact. */
  pinned?: boolean;
  /** One-line version for the "Also flagged" overflow line. */
  summary?: string;
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
  compact = false,
}: {
  items: AttentionItem[];
  asOf?: ReactNode;
  emptyText?: string;
  max?: number;
  /** Tighter cards for views where the strip shares the screen (Briefing). */
  compact?: boolean;
}) {
  const ranked = [...items].sort(
    (a, b) =>
      Number(!!b.pinned) - Number(!!a.pinned) ||
      SEVERITY_RANK[a.level] - SEVERITY_RANK[b.level],
  );
  const sorted = ranked.slice(0, max);
  const hidden = ranked.slice(max);
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
            <AttentionCard key={it.id} item={it} compact={compact} />
          ))}
        </ol>
      )}
      {hidden.length > 0 && (
        // Name what did not fit instead of a bare count, so nothing
        // important is silently out of view.
        <p className="mt-1.5 text-[12px] text-smoke-300">
          <span className="font-semibold">Also flagged:</span>{" "}
          {hidden.map(oneLine).join(" · ")}
        </p>
      )}
    </section>
  );
}

// Titles written to follow the number ("structures inside the cone") start
// lower-case; prefix the number for those, use the title alone otherwise.
function oneLine(it: AttentionItem): string {
  if (it.summary) return it.summary;
  const continues = /^[a-z]/.test(it.title);
  return continues && it.value ? `${it.value} ${it.unit ?? ""} ${it.title}`.replace(/\s+/g, " ") : it.title;
}

function AttentionCard({ item, compact }: { item: AttentionItem; compact?: boolean }) {
  const s = SEVERITY[item.level];
  return (
    <li
      className={`flex min-w-0 flex-col rounded-md border border-l-4 border-smoke-700 bg-smoke-800 ${
        compact ? "gap-1 px-3 py-2" : "gap-1.5 px-3.5 py-3"
      } ${s.edge}`}
    >
      <div>
        <SeverityBadge level={item.level} />
      </div>
      {item.value != null && (
        <div className="flex items-baseline gap-1.5">
          <span
            className={`${compact ? "text-[22px]" : "text-[28px]"} font-bold leading-none tabular-nums text-smoke-100`}
          >
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
        <div
          className={`text-[12px] leading-snug text-smoke-300 ${compact ? "line-clamp-2" : ""}`}
        >
          {item.detail}
        </div>
      )}
      {item.action && (
        <button
          onClick={item.action.onClick}
          className={`mt-auto inline-flex w-fit items-center gap-1 rounded border border-smoke-600 bg-smoke-700 font-semibold text-smoke-100 hover:border-ember-500 hover:bg-ember-900/40 ${
            compact ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-[12px]"
          }`}
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
