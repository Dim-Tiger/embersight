"use client";

import { useIncidents } from "@/lib/queries";
import { useStore } from "@/lib/store";
import {
  AlertTriangle,
  ChevronDown,
  FileText,
  Flame,
  LayoutGrid,
  Package,
  Route,
  Wind,
} from "lucide-react";
import { AgentFeed } from "./components/panels/AgentFeed";
import { ApprovalQueue } from "./components/panels/ApprovalQueue";
import { EvacuationTab } from "./components/panels/EvacuationTab";
import { IAPDraft } from "./components/panels/IAPDraft";
import { IncidentMap } from "./components/map/IncidentMap";
import { ResourcesTab } from "./components/panels/ResourcesTab";
import { ThreatsTab } from "./components/panels/ThreatsTab";
import { useAgentStream } from "./components/panels/useAgentStream";
import { WeatherTab } from "./components/panels/WeatherTab";

const NAV_ITEMS = [
  { tab: "Operations", Icon: LayoutGrid, label: "Operations" },
  { tab: "Weather", Icon: Wind, label: "Weather" },
  { tab: "Resources", Icon: Package, label: "Resources" },
  { tab: "Threats", Icon: AlertTriangle, label: "Threats" },
  { tab: "Evacuation", Icon: Route, label: "Evacuation" },
  { tab: "IAP", Icon: FileText, label: "IAP" },
] as const;

export default function Page() {
  const activeTab = useStore((s) => s.activeTab);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const setSelectedIncident = useStore((s) => s.setSelectedIncident);
  const { data: incidents } = useIncidents();
  const { start } = useAgentStream();

  const selectedIncident = incidents?.find((i) => i.id === selectedIncidentId);

  const handleIncidentChange = (id: string) => {
    setSelectedIncident(id || null);
    const inc = incidents?.find((i) => i.id === id);
    if (inc) start(inc);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-smoke-900">
      {/* Left Sidebar */}
      <nav className="flex w-56 flex-shrink-0 flex-col border-r border-smoke-700 bg-smoke-800">
        {/* Logo */}
        <div className="flex items-center gap-2 border-b border-smoke-700 px-4 py-4">
          <div className="h-2.5 w-2.5 flex-shrink-0 rounded-full bg-ember-500 shadow-[0_0_12px_#f97316]" />
          <span className="font-semibold tracking-wide text-smoke-200">
            EmberSight
          </span>
        </div>

        {/* Incident Selector */}
        <div className="border-b border-smoke-700 px-3 py-4">
          <label className="mb-1.5 block text-[10px] font-medium uppercase tracking-widest text-smoke-400">
            Active Incident
          </label>
          <div className="relative">
            <select
              value={selectedIncidentId ?? ""}
              onChange={(e) => handleIncidentChange(e.target.value)}
              className="w-full cursor-pointer appearance-none rounded bg-smoke-700 px-2.5 py-1.5 pr-7 text-xs text-smoke-200 focus:outline-none focus:ring-1 focus:ring-ember-500"
            >
              <option value="">— choose incident —</option>
              {incidents?.map((inc) => (
                <option key={inc.id} value={inc.id}>
                  {inc.name}
                  {inc.acres ? ` (${Math.round(inc.acres).toLocaleString()} ac)` : ""}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-smoke-400" />
          </div>
          {selectedIncident && (
            <div className="mt-2 space-y-0.5 text-[10px] leading-relaxed">
              {selectedIncident.contained_pct != null && (
                <div className="text-ember-300">
                  {Math.round(selectedIncident.contained_pct * 100)}% contained
                </div>
              )}
              {selectedIncident.acres != null && (
                <div className="text-smoke-400">
                  {Math.round(selectedIncident.acres).toLocaleString()} acres
                </div>
              )}
              {selectedIncident.started_at && (
                <div className="text-smoke-500">
                  Started{" "}
                  {new Date(selectedIncident.started_at).toLocaleDateString()}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Nav Items */}
        <div className="flex-1 py-2">
          {NAV_ITEMS.map(({ tab, Icon, label }) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              disabled={!selectedIncidentId}
              className={`flex w-full items-center gap-2.5 px-4 py-2.5 text-sm transition-colors ${
                activeTab === tab && selectedIncidentId
                  ? "border-r-2 border-ember-500 bg-ember-900/40 text-ember-200"
                  : selectedIncidentId
                    ? "text-smoke-400 hover:bg-smoke-700/50 hover:text-smoke-200"
                    : "cursor-not-allowed text-smoke-600"
              }`}
            >
              <Icon className="h-4 w-4 flex-shrink-0" />
              {label}
            </button>
          ))}
        </div>

        {/* Footer */}
        <div className="border-t border-smoke-700 px-3 py-2">
          <div className="text-center text-[9px] text-smoke-500">
            DRAFT · never dispatches
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="flex-1 overflow-hidden">
        {!selectedIncidentId ? (
          <NoIncidentState />
        ) : (
          <>
            {activeTab === "Operations" && <OperationsTab />}
            {activeTab === "Weather" && <WeatherTab />}
            {activeTab === "Resources" && <ResourcesTab />}
            {activeTab === "Threats" && <ThreatsTab />}
            {activeTab === "Evacuation" && <EvacuationTab />}
            {activeTab === "IAP" && <IAPDraft />}
          </>
        )}
      </main>
    </div>
  );
}

function NoIncidentState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-smoke-400">
      <Flame className="h-12 w-12 text-smoke-600" />
      <div className="text-center">
        <p className="text-sm font-medium text-smoke-300">No incident selected</p>
        <p className="mt-1 text-xs">
          Choose an active fire from the left panel to begin.
        </p>
      </div>
    </div>
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
