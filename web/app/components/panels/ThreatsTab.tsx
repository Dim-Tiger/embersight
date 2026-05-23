"use client";

export function ThreatsTab() {
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-3">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">
            Threats / Values at Risk
          </h2>
          <p className="text-xs text-smoke-400">
            MS Building Footprints + CMS hospitals + NCES schools clipped to
            the spread cone (pass 2).
          </p>
        </header>
        <div className="rounded border border-dashed border-smoke-700 px-3 py-6 text-center text-xs text-smoke-400">
          Tremor Metric tiles + sortable table render here when the
          Values-at-Risk agent emits an output.
        </div>
      </div>
    </div>
  );
}
