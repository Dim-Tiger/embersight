"use client";

import { useEvacZones } from "@/lib/queries";
import { useStore } from "@/lib/store";
import { AgentCard, KeyFindings } from "./AgentCard";

type EvacPayload = {
  zones_advisory?: string[];
  zones_watch?: string[];
  zones_order?: string[];
  routes?: Array<{ name?: string; status?: string }>;
  key_findings?: string[];
};

export function EvacuationTab() {
  const { data, isLoading, error } = useEvacZones();
  const evac = useStore((s) => s.agentOutputs.evacuation_intelligence);
  const evacStatus = useStore(
    (s) => s.agentStatuses.evacuation_intelligence ?? "pending",
  );

  const ep = (evac?.payload ?? {}) as EvacPayload;
  const live = data?.features ?? [];

  const grouped = useMemoGroup(live);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">Evacuation</h2>
          <p className="text-xs text-smoke-400">
            Live Cal OES CA_EVACUATIONS feed + recommendations from the
            Evacuation Intelligence agent. EmberSight only{" "}
            <span className="text-ember-200">PROPOSES</span> status changes.
          </p>
        </header>

        <AgentCard
          title="Evacuation Intelligence"
          subtitle="Phasing proposals and route status"
          status={evacStatus}
          output={evac}
        >
          {(ep.zones_order?.length ?? 0) +
            (ep.zones_advisory?.length ?? 0) +
            (ep.zones_watch?.length ?? 0) >
            0 && (
            <div className="grid grid-cols-3 gap-2 text-[11px]">
              <ZoneCol
                title="PROPOSED order"
                tone="red"
                items={ep.zones_order ?? []}
              />
              <ZoneCol
                title="PROPOSED warning"
                tone="amber"
                items={ep.zones_advisory ?? []}
              />
              <ZoneCol
                title="PROPOSED watch"
                tone="ember"
                items={ep.zones_watch ?? []}
              />
            </div>
          )}
          {ep.routes && ep.routes.length > 0 && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-smoke-400">
                Egress routes
              </div>
              <ul className="space-y-0.5 text-[11px] text-smoke-300">
                {ep.routes.map((r, i) => (
                  <li key={i}>
                    <span className="text-ember-200">{r.name}</span>
                    {r.status ? (
                      <span className="text-smoke-500"> — {r.status}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ep.key_findings && <KeyFindings items={ep.key_findings} />}
        </AgentCard>

        <section className="rounded-md border border-smoke-700 bg-smoke-800/40">
          <header className="flex items-center justify-between border-b border-smoke-700 px-4 py-2">
            <div>
              <h2 className="text-sm font-semibold text-smoke-100">
                Live Cal OES zones
              </h2>
              <p className="text-[10px] text-smoke-400">
                Statewide CA_EVACUATIONS aggregated view (Zonehaven schema)
              </p>
            </div>
            <span className="rounded bg-smoke-800 px-2 py-0.5 text-[10px] text-smoke-300">
              {live.length} zones
            </span>
          </header>
          <div className="p-4">
            {isLoading && (
              <div className="text-xs text-smoke-400">
                Loading Cal OES evac zones…
              </div>
            )}
            {error && (
              <div className="text-xs text-red-300">
                Failed to load evac zones: {String(error)}
              </div>
            )}
            {grouped.length > 0 && (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {grouped.map(([status, count]) => (
                  <div
                    key={status}
                    className="rounded border border-smoke-700 bg-smoke-900/60 px-3 py-2"
                  >
                    <div className="text-[9px] uppercase tracking-widest text-smoke-500">
                      {status}
                    </div>
                    <div className="mt-0.5 text-base font-semibold text-smoke-100">
                      {count.toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function ZoneCol({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "red" | "amber" | "ember";
}) {
  const toneClass =
    tone === "red"
      ? "text-red-300"
      : tone === "amber"
        ? "text-amber-300"
        : "text-ember-300";
  return (
    <div className="rounded border border-smoke-700 bg-smoke-900/60 p-2">
      <div className={`text-[10px] font-semibold uppercase ${toneClass}`}>
        {title} · {items.length}
      </div>
      <ul className="mt-1 space-y-0.5 text-smoke-300">
        {items.length === 0 ? (
          <li className="italic text-smoke-500">none</li>
        ) : (
          items.map((z) => <li key={z}>{z}</li>)
        )}
      </ul>
    </div>
  );
}

function useMemoGroup(
  features: Array<{ properties?: Record<string, unknown> }>,
): Array<[string, number]> {
  const counts: Record<string, number> = {};
  for (const f of features) {
    const status = String(
      f.properties?.STATUS ?? f.properties?.status ?? "UNKNOWN",
    );
    counts[status] = (counts[status] ?? 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}
