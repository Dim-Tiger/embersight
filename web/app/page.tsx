"use client";

import { useStore } from "@/lib/store";
import { AgentFeed } from "./components/panels/AgentFeed";
import { ApprovalQueue } from "./components/panels/ApprovalQueue";
import { EvacuationTab } from "./components/panels/EvacuationTab";
import { IAPDraft } from "./components/panels/IAPDraft";
import { ResourcesTab } from "./components/panels/ResourcesTab";
import { ThreatsTab } from "./components/panels/ThreatsTab";
import { WeatherTab } from "./components/panels/WeatherTab";
import { IncidentMap } from "./components/map/IncidentMap";

const TABS = [
  "Operations",
  "Weather",
  "Resources",
  "Threats",
  "Evacuation",
  "IAP",
] as const;
type Tab = (typeof TABS)[number];

export default function Page() {
  const activeTab = useStore((s) => s.activeTab);
  const setActiveTab = useStore((s) => s.setActiveTab);

  return (
    <div className="flex h-screen flex-col">
      <Header />
      <Tabs value={activeTab} onChange={(t) => setActiveTab(t)} />
      <main className="flex-1 overflow-hidden">
        {activeTab === "Operations" && <OperationsTab />}
        {activeTab === "Weather" && <WeatherTab />}
        {activeTab === "Resources" && <ResourcesTab />}
        {activeTab === "Threats" && <ThreatsTab />}
        {activeTab === "Evacuation" && <EvacuationTab />}
        {activeTab === "IAP" && <IAPDraft />}
      </main>
    </div>
  );
}

function Header() {
  return (
    <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800 px-6 py-3">
      <div className="flex items-center gap-3">
        <div className="h-3 w-3 rounded-full bg-ember-500 shadow-[0_0_12px_#f97316]" />
        <h1 className="text-lg font-semibold tracking-wide text-smoke-200">
          EmberSight
        </h1>
        <span className="rounded border border-ember-700 bg-ember-900/40 px-2 py-0.5 text-[10px] uppercase tracking-widest text-ember-200">
          DRAFT — never dispatches
        </span>
      </div>
      <div className="text-xs text-smoke-400">
        CAL FIRE IMT decision support · hackathon prototype
      </div>
    </header>
  );
}

function Tabs({ value, onChange }: { value: Tab; onChange: (t: Tab) => void }) {
  return (
    <nav className="flex border-b border-smoke-700 bg-smoke-800/60 px-4">
      {TABS.map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={`relative px-4 py-2 text-sm transition-colors ${
            value === t
              ? "text-ember-200"
              : "text-smoke-400 hover:text-smoke-200"
          }`}
        >
          {t}
          {value === t && (
            <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-ember-500" />
          )}
        </button>
      ))}
    </nav>
  );
}

function OperationsTab() {
  return (
    <div className="grid h-full grid-cols-[1fr_360px] gap-px bg-smoke-700">
      <div className="bg-smoke-900">
        <IncidentMap />
      </div>
      <aside className="flex flex-col gap-px bg-smoke-700">
        <div className="flex-1 overflow-hidden bg-smoke-900">
          <AgentFeed />
        </div>
        <div className="h-[40%] overflow-hidden bg-smoke-900">
          <ApprovalQueue />
        </div>
      </aside>
    </div>
  );
}
