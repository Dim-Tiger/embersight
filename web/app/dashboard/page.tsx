"use client";

import { useIncidents } from "@/lib/queries";
import { AGENT_ORDER, type AgentStatus, useStore } from "@/lib/store";
import { useTestMode } from "@/lib/testMode";
import { useTheme } from "@/lib/theme";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  FileText,
  Flame,
  LayoutGrid,
  Moon,
  Package,
  Radio,
  Route,
  Sun,
  Wind,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { DevPanel } from "../components/DevPanel";
import { ApprovalQueue } from "../components/panels/ApprovalQueue";
import { BriefingTab } from "../components/panels/BriefingTab";
import { ChatPanel } from "../components/panels/ChatPanel";
import { EvacuationTab } from "../components/panels/EvacuationTab";
import { IAPDraft } from "../components/panels/IAPDraft";
import { IncidentMap } from "../components/map/IncidentMap";
import { LiveFeed } from "../components/panels/LiveFeed";
import { PipelineLadder } from "../components/panels/PipelineLadder";
import { ResourcesTab } from "../components/panels/ResourcesTab";
import { ThreatsTab } from "../components/panels/ThreatsTab";
import { useAgentStream } from "../components/panels/useAgentStream";
import { WeatherTab } from "../components/panels/WeatherTab";

// Which subagents own the data on each tab. Used by the sidebar to render a
// per-tab status dot so the IC can see at-a-glance which tabs have work
// happening without having to navigate into each one.
const TAB_AGENTS: Record<string, readonly string[]> = {
  Operations: [...AGENT_ORDER],
  Briefing: [...AGENT_ORDER],
  Weather: ["weather_wind"],
  Resources: ["resource_recommendation", "routing_staging"],
  Threats: ["values_at_risk", "terrain_fuel", "spread_simulation"],
  Evacuation: ["evacuation_intelligence"],
  IAP: ["orchestrator", "master_ic"],
};

const NAV_ITEMS = [
  { tab: "Operations", Icon: LayoutGrid, label: "Operations" },
  { tab: "Briefing", Icon: Radio, label: "Briefing" },
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
  const restartCount = useStore((s) => s.restartCount);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const theme = useTheme((s) => s.theme);
  const toggleTheme = useTheme((s) => s.toggle);
  const {
    data: incidents,
    isLoading: incidentsLoading,
    isError: incidentsError,
    error: incidentsErrorObj,
  } = useIncidents();
  const { startBriefing } = useAgentStream();
  const incidentsEmpty =
    !incidentsLoading && !incidentsError && incidents?.length === 0;

  const selectedIncident = incidents?.find((i) => i.id === selectedIncidentId);

  // Single authoritative place that fires the INITIAL BRIEFING — runs the
  // full fan-out once per incident change (or explicit restart). Subsequent
  // user messages take the chat path through sendMessage() and do NOT
  // re-trigger this effect.
  const lastStartedRef = useRef<{ id: string; count: number } | null>(null);
  useEffect(() => {
    if (!selectedIncidentId || !incidents) return;
    if (
      lastStartedRef.current?.id === selectedIncidentId &&
      lastStartedRef.current?.count === restartCount
    ) return;
    const inc = incidents.find((i) => i.id === selectedIncidentId);
    if (!inc) return;
    lastStartedRef.current = { id: selectedIncidentId, count: restartCount };
    startBriefing(inc);
  }, [selectedIncidentId, restartCount, incidents, startBriefing]);

  const handleIncidentChange = (id: string) => {
    setSelectedIncident(id || null);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-smoke-900">
      <TestModeBanner />
      <DevPanel />
      {/* Left Sidebar */}
      <nav
        className={`flex flex-shrink-0 flex-col border-r border-smoke-700 bg-smoke-800 transition-[width] duration-200 ease-out ${
          sidebarCollapsed ? "w-14" : "w-56"
        }`}
      >
        {/* Header: logo + EmberSight name + collapse/expand toggle */}
        <div
          className={`flex items-center border-b border-smoke-700 ${
            sidebarCollapsed ? "flex-col gap-2 px-2 py-3" : "gap-2 px-3 py-3.5"
          }`}
        >
          {/* Brand → marketing landing at `/` (root redirects to the
              static landing page in public/landing/). Opens in a new
              tab so the IC keeps the live incident session intact. */}
          <a
            href="/"
            target="_blank"
            rel="noopener"
            title="About EmberSight"
            className={`flex min-w-0 items-center rounded transition-opacity hover:opacity-80 ${
              sidebarCollapsed ? "justify-center" : "flex-1 gap-2"
            }`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/brand/logo.png"
              alt="EmberSight"
              className="h-7 w-7 flex-shrink-0 object-contain"
              // The sidebar is always dark (bg-smoke-800 = #111722) regardless
              // of in-app theme, so the logo must always use the inverted variant
              // — invert flips the black outline to white, hue-rotate(180deg)
              // brings the orange/red flame back to its original hue.
              style={{ filter: "invert(1) hue-rotate(180deg)" }}
            />
            {!sidebarCollapsed && (
              <span className="flex-1 truncate font-semibold tracking-wide text-smoke-200">
                EmberSight
              </span>
            )}
          </a>
          <button
            onClick={() => setSidebarCollapsed((v) => !v)}
            className="flex h-6 w-6 items-center justify-center rounded text-smoke-400 transition-colors hover:bg-smoke-700 hover:text-smoke-200"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? (
              <ChevronRight className="h-3.5 w-3.5" />
            ) : (
              <X className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        {/* Vertical brand wordmark — keeps "EmberSight" visible when collapsed */}
        {sidebarCollapsed && (
          <div className="flex justify-center border-b border-smoke-700 py-3">
            <span
              className="select-none text-[10px] font-semibold uppercase tracking-[0.32em] text-smoke-300"
              style={{ writingMode: "vertical-rl" }}
            >
              EmberSight
            </span>
          </div>
        )}

        {/* Incident Selector — hidden when collapsed */}
        {!sidebarCollapsed && (
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
            {incidentsLoading && (
              <div className="mt-2 text-[10px] text-smoke-400">
                Loading incidents…
              </div>
            )}
            {incidentsError && (
              <div className="mt-2 text-[10px] leading-relaxed text-red-300">
                Couldn&apos;t load incidents.
                <div className="text-[10px] text-red-400/80">
                  {incidentsErrorObj instanceof Error
                    ? incidentsErrorObj.message
                    : "Check upstream CAL FIRE / WFIGS connectivity."}
                </div>
              </div>
            )}
            {incidentsEmpty && (
              <div className="mt-2 text-[10px] leading-relaxed text-smoke-400">
                No active CA incidents reported.
                <div className="text-[10px] text-smoke-500">
                  Upstream feeds returned zero results.
                </div>
              </div>
            )}
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
        )}

        {/* Nav Items */}
        <div className="flex-1 py-2">
          {NAV_ITEMS.map(({ tab, Icon, label }) => (
            <NavItem
              key={tab}
              tab={tab}
              label={label}
              Icon={Icon}
              active={activeTab === tab}
              enabled={!!selectedIncidentId}
              collapsed={sidebarCollapsed}
              onClick={() => setActiveTab(tab)}
            />
          ))}
        </div>

        {/* Footer */}
        {!sidebarCollapsed ? (
          <div className="flex items-center justify-between border-t border-smoke-700 px-3 py-2">
            <span className="text-[9px] text-smoke-500">
              DRAFT · never dispatches
            </span>
            <button
              onClick={toggleTheme}
              className="flex h-6 w-6 items-center justify-center rounded text-smoke-400 transition-colors hover:bg-smoke-700 hover:text-smoke-200"
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            >
              {theme === "dark" ? (
                <Sun className="h-3.5 w-3.5" />
              ) : (
                <Moon className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
        ) : (
          <div className="flex justify-center border-t border-smoke-700 py-2">
            <button
              onClick={toggleTheme}
              className="flex h-6 w-6 items-center justify-center rounded text-smoke-400 transition-colors hover:bg-smoke-700 hover:text-smoke-200"
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            >
              {theme === "dark" ? (
                <Sun className="h-3.5 w-3.5" />
              ) : (
                <Moon className="h-3.5 w-3.5" />
              )}
            </button>
          </div>
        )}
      </nav>

      {/* Main Content */}
      <main className="flex-1 overflow-hidden">
        {/* OperationsView (and its map) stays mounted at all times so the
            maplibregl.Map instance is never destroyed and recreated on tab
            switch. Destroying + recreating triggers a resize/reentry bug
            where the map freezes at an arbitrary position. We hide it with
            `hidden` (display:none) instead of unmounting it, then call
            map.resize() when it becomes visible again via the activeTab
            effect in IncidentMap. The right column is the only thing that
            swaps between no-incident and incident-selected. */}
        <div className={activeTab === "Operations" ? "h-full" : "hidden"}>
          <OperationsView
            hasIncident={!!selectedIncidentId}
            loading={incidentsLoading}
            error={incidentsError ? incidentsErrorObj : null}
            empty={incidentsEmpty}
          />
        </div>
        {activeTab !== "Operations" && (
          !selectedIncidentId ? (
            <NoIncidentState
              loading={incidentsLoading}
              error={incidentsError ? incidentsErrorObj : null}
              empty={incidentsEmpty}
            />
          ) : (
            <>
              {activeTab === "Briefing" && <BriefingTab />}
              {activeTab === "Weather" && <WeatherTab />}
              {activeTab === "Resources" && <ResourcesTab />}
              {activeTab === "Threats" && <ThreatsTab />}
              {activeTab === "Evacuation" && <EvacuationTab />}
              {activeTab === "IAP" && <IAPDraft />}
            </>
          )
        )}
      </main>
    </div>
  );
}

function NavItem({
  tab,
  label,
  Icon,
  active,
  enabled,
  collapsed,
  onClick,
}: {
  tab: string;
  label: string;
  Icon: typeof LayoutGrid;
  active: boolean;
  enabled: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  const statuses = useStore((s) => s.agentStatuses);
  const agents = TAB_AGENTS[tab] ?? [];

  const { combined, doneCount, runningCount } = useMemo(() => {
    let done = 0;
    let running = 0;
    let error = 0;
    for (const a of agents) {
      const st = statuses[a] ?? "pending";
      if (st === "done") done++;
      else if (st === "running") running++;
      else if (st === "error") error++;
    }
    let combined: AgentStatus = "pending";
    if (error > 0) combined = "error";
    else if (running > 0) combined = "running";
    else if (agents.length > 0 && done === agents.length) combined = "done";
    return { combined, doneCount: done, runningCount: running };
  }, [agents, statuses]);

  return (
    <button
      onClick={onClick}
      disabled={!enabled}
      title={collapsed ? label : undefined}
      className={`flex w-full items-center text-sm transition-colors ${
        collapsed ? "justify-center px-2 py-2.5" : "gap-2.5 px-4 py-2.5"
      } ${
        active && enabled
          ? "border-r-2 border-ember-500 bg-ember-900/40 text-ember-200"
          : enabled
            ? "text-smoke-400 hover:bg-smoke-700/50 hover:text-smoke-200"
            : "cursor-not-allowed text-smoke-600"
      }`}
    >
      <span className="relative flex flex-shrink-0 items-center justify-center">
        <Icon className="h-4 w-4" />
        {/* When collapsed, overlay a tiny status dot on the icon itself */}
        {collapsed && enabled && combined !== "pending" && (
          <span
            className={`absolute -right-1 -top-1 h-1.5 w-1.5 rounded-full ${
              combined === "running"
                ? "bg-ember-400"
                : combined === "done"
                  ? "bg-emerald-400/80"
                  : "bg-red-500"
            }`}
          />
        )}
      </span>
      {!collapsed && (
        <>
          <span className="flex-1 text-left">{label}</span>
          {enabled && <NavDot status={combined} />}
          {enabled && combined === "running" && runningCount > 0 && (
            <span className="font-mono text-[9px] text-ember-400">
              {runningCount}
            </span>
          )}
          {enabled &&
            combined === "done" &&
            agents.length > 0 &&
            doneCount === agents.length && (
              <span className="font-mono text-[9px] text-emerald-400/70">
                {doneCount}
              </span>
            )}
        </>
      )}
    </button>
  );
}

function NavDot({ status }: { status: AgentStatus }) {
  if (status === "running") {
    return (
      <span className="relative flex h-2 w-2 flex-shrink-0">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-ember-400/70" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-ember-400" />
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="h-2 w-2 flex-shrink-0 rounded-full bg-emerald-400/80" />
    );
  }
  if (status === "error") {
    return <span className="h-2 w-2 flex-shrink-0 rounded-full bg-red-500" />;
  }
  return (
    <span className="h-2 w-2 flex-shrink-0 rounded-full bg-smoke-600 ring-1 ring-smoke-700" />
  );
}

function TestModeBanner() {
  const enabled = useTestMode((s) => s.enabled);
  const placement = useTestMode((s) => s.placementMode);
  if (!enabled) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-30 flex justify-center">
      <div className="pointer-events-auto mt-2 flex items-center gap-2 rounded-full border border-amber-500/60 bg-amber-500/15 px-3 py-1 text-[11px] font-medium text-amber-200 shadow-lg backdrop-blur">
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-300" />
        TEST MODE — using synthetic data
        {placement && (
          <span className="ml-1 rounded bg-amber-500/30 px-1.5 py-px text-[10px] text-amber-100">
            click map to place fire
          </span>
        )}
      </div>
    </div>
  );
}

function NoIncidentState({
  loading,
  error,
  empty,
}: {
  loading: boolean;
  error: unknown;
  empty: boolean;
}) {
  // Render the statewide map immediately so users can click a fire to
  // select it on first open — the sidebar dropdown is not the only way in.
  // The overlay pill reflects the upstream-feed state so the user knows why
  // the dropdown might be empty or stale.
  let headline = (
    <>
      Click an{" "}
      <span className="font-semibold text-ember-300">active fire</span> on the
      map, or choose one from the left panel, to begin.
    </>
  );
  let detail: string | null = null;
  let tone: "default" | "warn" | "error" = "default";

  if (loading) {
    headline = <>Loading incidents…</>;
    detail = "Fetching the latest active fires from CAL FIRE and WFIGS.";
  } else if (error) {
    headline = <>Couldn&apos;t load incidents</>;
    detail =
      error instanceof Error
        ? error.message
        : "The upstream incident feeds (CAL FIRE / WFIGS) are unreachable.";
    tone = "error";
  } else if (empty) {
    headline = <>No active CA incidents right now</>;
    detail =
      "Both CAL FIRE and WFIGS returned zero current incidents in California. The dashboard will populate when a new fire is reported.";
    tone = "warn";
  }

  const pillBorder =
    tone === "error"
      ? "border-red-500/50"
      : tone === "warn"
        ? "border-amber-500/40"
        : "border-ember-500/40";
  const iconColor =
    tone === "error"
      ? "text-red-400"
      : tone === "warn"
        ? "text-amber-300"
        : "text-ember-400";

  return (
    <div className="relative h-full w-full">
      <IncidentMap />
      <div className="pointer-events-none absolute left-1/2 top-6 z-10 -translate-x-1/2">
        <div
          className={`flex max-w-md flex-col gap-1 rounded-2xl border ${pillBorder} bg-smoke-800/90 px-4 py-2 text-xs text-smoke-200 shadow-lg backdrop-blur`}
        >
          <div className="flex items-center gap-2">
            <Flame className={`h-3.5 w-3.5 ${iconColor}`} />
            <span>{headline}</span>
          </div>
          {detail && (
            <p className="pl-5 text-[10px] leading-relaxed text-smoke-400">
              {detail}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Operations view: the grid layout is identical whether or not a fire
 * is selected. The map sits in the 1fr column in a stable DOM location
 * so it's never unmounted on incident click. Only the contents of the
 * right column swap — either the no-incident pill / loading / error
 * state, or the four operations panels.
 *
 * This shape exists because the prior split (NoIncidentState rendered
 * a full-width map, OperationsTab rendered a smaller grid map) caused
 * a brand-new MapLibre instance to be created inside a different-width
 * container on every incident click. The reflow during that transition
 * fired MapLibre's internal ResizeObserver multiple times, the task
 * scheduler reentered, and the map froze.
 */
function OperationsView({
  hasIncident,
  loading,
  error,
  empty,
}: {
  hasIncident: boolean;
  loading: boolean;
  error: Error | null;
  empty: boolean;
}) {
  return (
    <div className="grid h-full min-h-0 grid-cols-[1fr_360px] grid-rows-[minmax(0,1fr)] gap-px bg-smoke-700">
      <div className="relative min-h-0 bg-smoke-900">
        <IncidentMap />
        {/* Overlay the no-incident pill on top of the map without
           unmounting/remounting it. The pill is non-interactive so map
           clicks still work for the user to drop a synthetic ignition. */}
        {!hasIncident && (
          <NoIncidentOverlay loading={loading} error={error} empty={empty} />
        )}
      </div>
      <aside
        className="grid min-h-0 gap-px bg-smoke-700"
        style={{
          gridTemplateRows:
            "minmax(120px, 22%) minmax(0, 1fr) minmax(80px, 14%) minmax(80px, 22%)",
        }}
      >
        <div className="min-h-0 overflow-hidden bg-smoke-900">
          <PipelineLadder />
        </div>
        <div className="min-h-0 overflow-hidden bg-smoke-900">
          <ChatPanel />
        </div>
        <div className="min-h-0 overflow-hidden bg-smoke-900">
          <LiveFeed />
        </div>
        <div className="min-h-0 overflow-hidden bg-smoke-900">
          <ApprovalQueue />
        </div>
      </aside>
    </div>
  );
}

/**
 * The pill that sits over the map before an incident is selected.
 * Extracted from NoIncidentState so the SAME visual can be overlaid on
 * the persistent map without going through the unmount path.
 */
function NoIncidentOverlay({
  loading,
  error,
  empty,
}: {
  loading: boolean;
  error: Error | null;
  empty: boolean;
}) {
  let headline = (
    <>
      Click an{" "}
      <span className="font-semibold text-ember-300">active fire</span> on the
      map, or choose one from the left panel, to begin.
    </>
  );
  let detail: string | null = null;
  let tone: "default" | "warn" | "error" = "default";

  if (loading) {
    headline = <>Loading incidents…</>;
    detail = "Fetching the latest active fires from CAL FIRE and WFIGS.";
  } else if (error) {
    headline = <>Couldn&apos;t load incidents</>;
    detail =
      error instanceof Error
        ? error.message
        : "The upstream incident feeds (CAL FIRE / WFIGS) are unreachable.";
    tone = "error";
  } else if (empty) {
    headline = <>No active CA incidents right now</>;
    detail =
      "Both CAL FIRE and WFIGS returned zero current incidents in California. The dashboard will populate when a new fire is reported.";
    tone = "warn";
  }

  const pillBorder =
    tone === "error"
      ? "border-red-500/50"
      : tone === "warn"
        ? "border-amber-500/40"
        : "border-ember-500/40";
  const iconColor =
    tone === "error"
      ? "text-red-400"
      : tone === "warn"
        ? "text-amber-300"
        : "text-ember-400";

  return (
    <div className="pointer-events-none absolute left-1/2 top-6 z-10 -translate-x-1/2">
      <div
        className={`flex max-w-md flex-col gap-1 rounded-2xl border ${pillBorder} bg-smoke-800/90 px-4 py-2 text-xs text-smoke-200 shadow-lg backdrop-blur`}
      >
        <div className="flex items-center gap-2">
          <Flame className={`h-3.5 w-3.5 ${iconColor}`} />
          <span>{headline}</span>
        </div>
        {detail && (
          <p className="pl-5 text-[10px] leading-relaxed text-smoke-400">
            {detail}
          </p>
        )}
      </div>
    </div>
  );
}

// Kept for non-Operations tabs (Briefing/Weather/Resources/etc) when no
// incident is selected. Renders its OWN map; this branch is intentional
// because if the user is on, say, Briefing tab without an incident, we
// still want them to see the map and be able to click a fire. The
// stable-DOM map only applies to the Operations tab.
function OperationsTab() {
  // Legacy alias retained for clarity. Kept for any imports lingering
  // in dev branches. The real layout lives in <OperationsView>.
  return (
    <OperationsView
      hasIncident={true}
      loading={false}
      error={null}
      empty={false}
    />
  );
}
