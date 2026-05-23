"use client";

import { useIncidents, useWeather } from "@/lib/queries";
import { useStore } from "@/lib/store";

export function WeatherTab() {
  const id = useStore((s) => s.selectedIncidentId);
  const { data: incidents } = useIncidents();
  const incident = incidents?.find((i) => i.id === id);
  const { data: wx, isLoading } = useWeather(
    incident?.lat ?? null,
    incident?.lon ?? null,
  );

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">Weather</h2>
          <p className="text-xs text-smoke-400">
            NWS active alerts now; HRRR/RTMA AreaChart + Visx wind rose land in
            pass 2.
          </p>
        </header>

        {!incident && (
          <div className="rounded border border-dashed border-smoke-700 px-3 py-6 text-center text-xs text-smoke-400">
            Select an incident first.
          </div>
        )}
        {incident && isLoading && (
          <div className="text-xs text-smoke-400">Fetching NWS alerts…</div>
        )}
        {wx && (
          <section>
            <h3 className="mb-2 text-sm font-semibold text-ember-200">
              NWS active alerts at {incident?.name}
            </h3>
            <pre className="overflow-auto rounded bg-smoke-800 p-4 text-[11px] leading-relaxed text-smoke-200">
              {JSON.stringify(wx, null, 2)}
            </pre>
          </section>
        )}
      </div>
    </div>
  );
}
