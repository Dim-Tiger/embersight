"use client";

import { useEvacZones } from "@/lib/queries";

export function EvacuationTab() {
  const { data, isLoading, error } = useEvacZones();

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-3">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">Evacuation</h2>
          <p className="text-xs text-smoke-400">
            Cal OES CA_EVACUATIONS — zones per-status. EmberSight only{" "}
            <span className="text-ember-200">PROPOSES</span> status changes.
          </p>
        </header>

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
        {data && (
          <pre className="max-h-[60vh] overflow-auto rounded bg-smoke-800 p-4 text-[11px] leading-relaxed text-smoke-200">
            {JSON.stringify(
              {
                type: data.type,
                feature_count: data.features?.length ?? 0,
                sample: data.features?.slice(0, 3),
              },
              null,
              2,
            )}
          </pre>
        )}
      </div>
    </div>
  );
}
