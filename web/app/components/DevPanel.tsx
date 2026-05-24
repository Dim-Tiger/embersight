"use client";

import { useStore } from "@/lib/store";
import {
  WEATHER_ALERT_LABELS,
  type WeatherAlertPreset,
  useTestMode,
} from "@/lib/testMode";
import {
  Beaker,
  Crosshair,
  Flame,
  Trash2,
  Wind,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

/**
 * Floating dev panel for driving the dashboard against synthetic data.
 *
 * State lives in [[useTestMode]] (localStorage-backed). When enabled, the
 * incident / weather / wind hooks return synthetic payloads instead of
 * (or alongside) the live feeds.
 *
 * The panel is always mountable in dev — `next dev` is the gate. In a
 * production build we still mount it; hiding it behind NODE_ENV would
 * surprise users running their own deploy who want to demo without real
 * fires. The big TEST MODE banner makes it obvious when it's on.
 */
export function DevPanel() {
  const [open, setOpen] = useState(false);
  const hydrate = useTestMode((s) => s.hydrate);
  const hydrated = useTestMode((s) => s.hydrated);
  const enabled = useTestMode((s) => s.enabled);
  const setEnabled = useTestMode((s) => s.setEnabled);
  const synthetic = useTestMode((s) => s.syntheticIncidents);
  const removeIncident = useTestMode((s) => s.removeIncident);
  const clearIncidents = useTestMode((s) => s.clearIncidents);
  const draftName = useTestMode((s) => s.draftName);
  const setDraftName = useTestMode((s) => s.setDraftName);
  const draftAcres = useTestMode((s) => s.draftAcres);
  const setDraftAcres = useTestMode((s) => s.setDraftAcres);
  const placementMode = useTestMode((s) => s.placementMode);
  const setPlacementMode = useTestMode((s) => s.setPlacementMode);
  const wind = useTestMode((s) => s.wind);
  const setWind = useTestMode((s) => s.setWind);
  const alertPreset = useTestMode((s) => s.alertPreset);
  const setAlertPreset = useTestMode((s) => s.setAlertPreset);
  const addIncident = useTestMode((s) => s.addIncident);
  const reset = useTestMode((s) => s.reset);

  const setSelectedIncident = useStore((s) => s.setSelectedIncident);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  if (!hydrated) return null;

  return (
    <>
      {/* Launcher button — bottom-right, always visible */}
      <button
        onClick={() => setOpen((v) => !v)}
        className={`fixed bottom-4 right-4 z-40 flex h-10 items-center gap-2 rounded-full border px-3.5 text-xs font-medium shadow-lg backdrop-blur transition ${
          enabled
            ? "border-amber-500/60 bg-amber-500/15 text-amber-200 hover:bg-amber-500/25"
            : "border-smoke-600 bg-smoke-800/90 text-smoke-300 hover:bg-smoke-700"
        }`}
        title={enabled ? "Test mode is ON" : "Open test utility"}
      >
        <Beaker className="h-3.5 w-3.5" />
        <span>{enabled ? "TEST MODE" : "Test Utility"}</span>
      </button>

      {open && (
        <div className="fixed inset-y-0 right-0 z-50 flex w-[380px] flex-col border-l border-smoke-700 bg-smoke-900 shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-smoke-700 px-4 py-3">
            <div className="flex items-center gap-2">
              <Beaker className="h-4 w-4 text-ember-400" />
              <span className="text-sm font-semibold text-smoke-200">
                Test Utility
              </span>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="flex h-6 w-6 items-center justify-center rounded text-smoke-400 hover:bg-smoke-700 hover:text-smoke-200"
              aria-label="Close"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Master toggle */}
          <div className="border-b border-smoke-700 px-4 py-3">
            <DataSourceToggle enabled={enabled} onChange={setEnabled} />
            <p className="mt-2 text-[10px] leading-relaxed text-smoke-400">
              Test mode replaces live incidents/weather/wind on the client.
              The agent backend still uses real upstream data on its end.
            </p>
          </div>

          {/* Body — scrollable */}
          <div className="flex-1 overflow-y-auto">
            <Section title="Spawn a Fire" icon={<Flame className="h-3.5 w-3.5" />}>
              <div className="grid grid-cols-[1fr_88px] gap-2">
                <LabeledInput
                  label="Name"
                  value={draftName}
                  onChange={setDraftName}
                  placeholder="Test Ridge Fire"
                />
                <LabeledNumber
                  label="Acres"
                  value={draftAcres}
                  onChange={setDraftAcres}
                  step={50}
                  min={0}
                />
              </div>

              <button
                onClick={() => {
                  if (!enabled) setEnabled(true);
                  setPlacementMode(!placementMode);
                }}
                className={`mt-3 flex w-full items-center justify-center gap-1.5 rounded border px-3 py-2 text-xs transition ${
                  placementMode
                    ? "border-ember-500 bg-ember-500/15 text-ember-200"
                    : "border-smoke-600 bg-smoke-800 text-smoke-200 hover:bg-smoke-700"
                }`}
              >
                <Crosshair className="h-3.5 w-3.5" />
                {placementMode
                  ? "Click map to place — click here to cancel"
                  : "Click map to place fire"}
              </button>

              {/* Quick presets */}
              <div className="mt-2 grid grid-cols-3 gap-1.5">
                <QuickSpawnButton
                  label="Los Padres"
                  lat={34.7402}
                  lon={-119.3142}
                  onSpawn={(lat, lon) => {
                    if (!enabled) setEnabled(true);
                    const inc = addIncident(lat, lon);
                    setSelectedIncident(inc.id);
                  }}
                />
                <QuickSpawnButton
                  label="Tahoe"
                  lat={38.9399}
                  lon={-120.0427}
                  onSpawn={(lat, lon) => {
                    if (!enabled) setEnabled(true);
                    const inc = addIncident(lat, lon);
                    setSelectedIncident(inc.id);
                  }}
                />
                <QuickSpawnButton
                  label="Malibu"
                  lat={34.0259}
                  lon={-118.7798}
                  onSpawn={(lat, lon) => {
                    if (!enabled) setEnabled(true);
                    const inc = addIncident(lat, lon);
                    setSelectedIncident(inc.id);
                  }}
                />
              </div>

              {/* Existing synthetics */}
              {synthetic.length > 0 && (
                <div className="mt-3 space-y-1">
                  <div className="flex items-center justify-between text-[10px] uppercase tracking-widest text-smoke-400">
                    <span>Active ({synthetic.length})</span>
                    <button
                      onClick={clearIncidents}
                      className="text-[10px] text-smoke-400 hover:text-red-300"
                    >
                      clear all
                    </button>
                  </div>
                  {synthetic.map((s) => (
                    <div
                      key={s.id}
                      className="flex items-center justify-between rounded border border-smoke-700 bg-smoke-800 px-2 py-1.5 text-xs"
                    >
                      <button
                        onClick={() => setSelectedIncident(s.id)}
                        className="flex-1 truncate text-left text-smoke-200 hover:text-ember-300"
                      >
                        {s.name}
                        <span className="ml-1.5 text-[10px] text-smoke-400">
                          {s.lat.toFixed(3)}, {s.lon.toFixed(3)}
                        </span>
                      </button>
                      <button
                        onClick={() => removeIncident(s.id)}
                        className="ml-2 flex h-5 w-5 items-center justify-center rounded text-smoke-400 hover:bg-red-500/20 hover:text-red-300"
                        aria-label="Remove"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </Section>

            <Section title="Wind" icon={<Wind className="h-3.5 w-3.5" />}>
              <div className="space-y-3">
                <Slider
                  label="Speed"
                  value={wind.speed_ms}
                  min={0}
                  max={30}
                  step={0.5}
                  unit="m/s"
                  onChange={(v) => setWind({ speed_ms: v })}
                />
                <Slider
                  label="Gusts"
                  value={wind.gusts_ms ?? 0}
                  min={0}
                  max={45}
                  step={0.5}
                  unit="m/s"
                  onChange={(v) => setWind({ gusts_ms: v || null })}
                />
                <div>
                  <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-widest text-smoke-400">
                    <span>Direction (from)</span>
                    <span className="font-mono text-smoke-300">
                      {wind.direction_deg}° {compassFrom(wind.direction_deg)}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={359}
                    step={1}
                    value={wind.direction_deg}
                    onChange={(e) =>
                      setWind({ direction_deg: Number(e.target.value) })
                    }
                    className="w-full accent-ember-500"
                  />
                  <div className="mt-1 grid grid-cols-8 gap-1">
                    {COMPASS_POINTS.map(([deg, label]) => (
                      <button
                        key={label}
                        onClick={() => setWind({ direction_deg: deg })}
                        className={`rounded py-0.5 text-[10px] ${
                          wind.direction_deg === deg
                            ? "bg-ember-500/30 text-ember-200"
                            : "bg-smoke-800 text-smoke-400 hover:bg-smoke-700"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </Section>

            <Section title="Weather Alert">
              <div className="space-y-1">
                {(Object.keys(WEATHER_ALERT_LABELS) as WeatherAlertPreset[]).map(
                  (k) => (
                    <button
                      key={k}
                      onClick={() => setAlertPreset(k)}
                      className={`flex w-full items-center justify-between rounded border px-2.5 py-1.5 text-xs ${
                        alertPreset === k
                          ? "border-ember-500 bg-ember-500/15 text-ember-200"
                          : "border-smoke-700 bg-smoke-800 text-smoke-200 hover:bg-smoke-700"
                      }`}
                    >
                      <span>{WEATHER_ALERT_LABELS[k]}</span>
                      {alertPreset === k && (
                        <span className="text-[10px] text-ember-300">active</span>
                      )}
                    </button>
                  ),
                )}
              </div>
            </Section>
          </div>

          {/* Footer */}
          <div className="border-t border-smoke-700 px-4 py-3">
            <button
              onClick={() => {
                reset();
              }}
              className="w-full rounded border border-smoke-700 px-3 py-1.5 text-xs text-smoke-300 hover:bg-smoke-800"
            >
              Reset to defaults
            </button>
          </div>
        </div>
      )}
    </>
  );
}

function DataSourceToggle({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (b: boolean) => void;
}) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded border border-smoke-600">
      <button
        onClick={() => onChange(false)}
        className={`px-3 py-2 text-xs font-medium transition ${
          !enabled
            ? "bg-emerald-500/15 text-emerald-200"
            : "bg-smoke-800 text-smoke-400 hover:bg-smoke-700"
        }`}
      >
        Real Data
      </button>
      <button
        onClick={() => onChange(true)}
        className={`px-3 py-2 text-xs font-medium transition ${
          enabled
            ? "bg-amber-500/20 text-amber-200"
            : "bg-smoke-800 text-smoke-400 hover:bg-smoke-700"
        }`}
      >
        Test Data
      </button>
    </div>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-smoke-800 px-4 py-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-widest text-smoke-400">
        {icon}
        <span>{title}</span>
      </div>
      {children}
    </div>
  );
}

function LabeledInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-smoke-400">
        {label}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="rounded border border-smoke-700 bg-smoke-800 px-2 py-1.5 text-xs text-smoke-200 focus:border-ember-500 focus:outline-none"
      />
    </label>
  );
}

function LabeledNumber({
  label,
  value,
  onChange,
  step = 1,
  min,
  max,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  step?: number;
  min?: number;
  max?: number;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-smoke-400">
        {label}
      </span>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(e) => onChange(Number(e.target.value))}
        className="rounded border border-smoke-700 bg-smoke-800 px-2 py-1.5 text-xs text-smoke-200 focus:border-ember-500 focus:outline-none"
      />
    </label>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (n: number) => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-widest text-smoke-400">
        <span>{label}</span>
        <span className="font-mono text-smoke-300">
          {value.toFixed(1)} {unit}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-ember-500"
      />
    </div>
  );
}

function QuickSpawnButton({
  label,
  lat,
  lon,
  onSpawn,
}: {
  label: string;
  lat: number;
  lon: number;
  onSpawn: (lat: number, lon: number) => void;
}) {
  return (
    <button
      onClick={() => onSpawn(lat, lon)}
      className="rounded border border-smoke-700 bg-smoke-800 px-2 py-1.5 text-[11px] text-smoke-200 hover:bg-smoke-700"
      title={`Spawn @ ${lat.toFixed(3)}, ${lon.toFixed(3)}`}
    >
      {label}
    </button>
  );
}

const COMPASS_POINTS: Array<[number, string]> = [
  [0, "N"],
  [45, "NE"],
  [90, "E"],
  [135, "SE"],
  [180, "S"],
  [225, "SW"],
  [270, "W"],
  [315, "NW"],
];

function compassFrom(deg: number): string {
  const i = Math.round(((deg % 360) + 360) % 360 / 22.5) % 16;
  return [
    "N","NNE","NE","ENE","E","ESE","SE","SSE",
    "S","SSW","SW","WSW","W","WNW","NW","NNW",
  ][i];
}
