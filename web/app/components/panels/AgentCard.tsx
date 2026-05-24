"use client";

import { type AgentOutput, type AgentStatus, useStore } from "@/lib/store";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2 } from "lucide-react";
import { type ReactNode } from "react";

export function AgentCard({
  title,
  subtitle,
  status,
  output,
  children,
}: {
  title: string;
  subtitle?: string;
  status: AgentStatus;
  output: AgentOutput | undefined;
  children?: ReactNode;
}) {
  // When the agent stream itself has errored before this agent ran, the local
  // `status` is still "pending" but the global error tells the real story.
  // Surface that instead of the generic "Waiting for the agent to start…" copy.
  const streamError = useStore((s) => s.errorMessage);
  const hasStreamError = status === "pending" && !!streamError;
  return (
    <section className="rounded-md border border-smoke-700 bg-smoke-800/40">
      <header className="flex items-center justify-between border-b border-smoke-700 px-4 py-2">
        <div>
          <h2 className="text-sm font-semibold text-smoke-100">{title}</h2>
          {subtitle && (
            <p className="text-[10px] text-smoke-400">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={status} streamError={hasStreamError} />
          {output?.confidence != null && status === "done" && (
            <span className="rounded bg-ember-900/40 px-2 py-0.5 text-[10px] font-semibold text-ember-200 ring-1 ring-ember-800/60">
              {Math.round(output.confidence * 100)}% conf
            </span>
          )}
        </div>
      </header>

      <div className="space-y-3 px-4 py-3">
        {status === "pending" && hasStreamError && (
          <div className="rounded border border-red-800/60 bg-red-900/20 px-2.5 py-1.5 text-[11px] text-red-200">
            <div className="flex items-center gap-1 font-medium">
              <AlertTriangle className="h-3 w-3" />
              Agent service unavailable
            </div>
            <div className="mt-0.5 break-words text-[10px] text-red-300/90">
              {streamError}
            </div>
            <div className="mt-1 text-[10px] text-red-300/70">
              Use &ldquo;Re-run stream&rdquo; in the Agent activity panel once the service is back.
            </div>
          </div>
        )}
        {status === "pending" && !hasStreamError && (
          <p className="text-[11px] italic text-smoke-500">
            Waiting for the agent to start. Pick an incident and the team runs
            automatically.
          </p>
        )}
        {status === "running" && !output && <RunningSkeleton />}
        {status === "running" && output && (
          <div className="flex items-center gap-1.5 text-[11px] text-ember-300">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ember-400" />
            Streaming new findings…
          </div>
        )}
        {status === "error" && (
          <p className="text-[11px] text-red-300">
            Agent reported an error. See the live stream in the Agent activity
            panel.
          </p>
        )}

        {output && (
          <>
            {output.narrative && (
              <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-smoke-200">
                {stripPrefix(output.narrative)}
              </p>
            )}
            {output.confidence_driver && (
              <p className="text-[10px] text-smoke-500">
                Confidence driver: {output.confidence_driver}
              </p>
            )}
            {children}
            {output.citation_bundle?.datasets &&
              output.citation_bundle.datasets.length > 0 && (
                <details className="text-[10px] text-smoke-400">
                  <summary className="cursor-pointer text-smoke-500">
                    Citations · {output.citation_bundle.datasets.length} sources
                  </summary>
                  <ul className="mt-1 space-y-0.5 pl-3">
                    {output.citation_bundle.datasets.map((d, i) => (
                      <li key={i} className="truncate">
                        {d.url ? (
                          <a
                            href={d.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-ember-300 hover:underline"
                          >
                            {d.name || d.url}
                          </a>
                        ) : (
                          d.name || "(unnamed)"
                        )}
                        {d.version ? (
                          <span className="text-smoke-500"> · {d.version}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
          </>
        )}
      </div>
    </section>
  );
}

export function MetricGrid({
  items,
}: {
  items: { label: string; value: string | number; hint?: string }[];
}) {
  if (items.length === 0) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {items.map((it) => (
        <div
          key={it.label}
          className="rounded border border-smoke-700 bg-smoke-900/60 px-3 py-2"
        >
          <div className="text-[9px] uppercase tracking-widest text-smoke-500">
            {it.label}
          </div>
          <div className="mt-0.5 text-base font-semibold text-smoke-100">
            {it.value}
          </div>
          {it.hint && (
            <div className="text-[10px] text-smoke-500">{it.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
}

export function KeyFindings({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
        Key findings
      </div>
      <ul className="space-y-1 text-[11px] text-smoke-300">
        {items.map((f, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="text-ember-400">▸</span>
            <span>{f}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusBadge({
  status,
  streamError,
}: {
  status: AgentStatus;
  streamError?: boolean;
}) {
  if (status === "running") {
    return (
      <span className="flex items-center gap-1 rounded bg-ember-900/40 px-2 py-0.5 text-[10px] font-medium text-ember-200">
        <Loader2 className="h-3 w-3 animate-spin" />
        running
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="flex items-center gap-1 rounded bg-emerald-900/40 px-2 py-0.5 text-[10px] font-medium text-emerald-200">
        <CheckCircle2 className="h-3 w-3" />
        done
      </span>
    );
  }
  if (streamError) {
    return (
      <span className="flex items-center gap-1 rounded bg-red-900/40 px-2 py-0.5 text-[10px] font-medium text-red-200">
        <AlertTriangle className="h-3 w-3" />
        unavailable
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 rounded bg-smoke-800 px-2 py-0.5 text-[10px] font-medium text-smoke-400">
      <CircleDashed className="h-3 w-3" />
      pending
    </span>
  );
}

function stripPrefix(s: string): string {
  return s.replace(/^\[[\w_]+\]\s*/, "").trim();
}

function RunningSkeleton() {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-[11px] text-ember-300">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span className="italic">Agent gathering data…</span>
      </div>
      <div className="space-y-1.5">
        <SkeletonBar widthPct={92} />
        <SkeletonBar widthPct={78} />
        <SkeletonBar widthPct={64} />
      </div>
      <div className="grid grid-cols-3 gap-2">
        <SkeletonTile />
        <SkeletonTile />
        <SkeletonTile />
      </div>
    </div>
  );
}

function SkeletonBar({ widthPct }: { widthPct: number }) {
  return (
    <div
      className="h-2 animate-pulse rounded bg-smoke-800/80"
      style={{ width: `${widthPct}%` }}
    />
  );
}

function SkeletonTile() {
  return (
    <div className="h-14 animate-pulse rounded border border-smoke-700/60 bg-smoke-900/60" />
  );
}
