"use client";

import { type AgentOutput, type AgentStatus } from "@/lib/store";
import { CheckCircle2, CircleDashed, Loader2 } from "lucide-react";
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
          <StatusBadge status={status} />
          {output?.confidence != null && status === "done" && (
            <span className="rounded bg-ember-900/40 px-2 py-0.5 text-[10px] font-semibold text-ember-200 ring-1 ring-ember-800/60">
              {Math.round(output.confidence * 100)}% conf
            </span>
          )}
        </div>
      </header>

      <div className="space-y-3 px-4 py-3">
        {status === "pending" && (
          <p className="text-[11px] italic text-smoke-500">
            Waiting for the agent to start. Pick an incident and the team runs
            automatically.
          </p>
        )}
        {status === "running" && (
          <p className="text-[11px] italic text-ember-300">
            Agent is running — partial output will appear when it finishes its
            step.
          </p>
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

function StatusBadge({ status }: { status: AgentStatus }) {
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
