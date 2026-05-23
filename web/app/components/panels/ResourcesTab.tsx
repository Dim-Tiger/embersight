"use client";

export function ResourcesTab() {
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-3">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">
            Resources — RECOMMEND
          </h2>
          <p className="text-xs text-smoke-400">
            Header verb is always{" "}
            <span className="text-ember-200">RECOMMEND</span> /{" "}
            <span className="text-ember-200">PROPOSED</span>. EmberSight never
            dispatches.
          </p>
        </header>
        <div className="rounded border border-dashed border-smoke-700 px-3 py-6 text-center text-xs text-smoke-400">
          Tremor BarList of proposed resources renders here when the Resource
          Recommendation agent emits an output (pass 2).
        </div>
      </div>
    </div>
  );
}
