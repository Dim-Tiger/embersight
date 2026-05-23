"use client";

import { create } from "zustand";

export type Tab =
  | "Operations"
  | "Weather"
  | "Resources"
  | "Threats"
  | "Evacuation"
  | "IAP";

export type AgentEvent = {
  ts: number;
  kind: string;
  name?: string | null;
  data?: unknown;
  run_id?: string;
};

export type PendingInterrupt = {
  thread_id: string;
  interrupt: {
    id?: string;
    type:
      | "iap_approval"
      | "resource_recommendation"
      | "evac_zone_change"
      | "trigger_point_violation";
    payload?: Record<string, unknown>;
    created_at?: string;
  };
};

export type MapViewport = {
  longitude: number;
  latitude: number;
  zoom: number;
};

export type Store = {
  selectedIncidentId: string | null;
  selectedThreadId: string | null;
  activeTab: Tab;
  mapViewport: MapViewport;
  operationalPeriod: number;
  agentEvents: AgentEvent[];
  pendingInterrupts: PendingInterrupt[];
  setSelectedIncident: (id: string | null) => void;
  setSelectedThread: (id: string | null) => void;
  setActiveTab: (t: Tab) => void;
  setViewport: (v: MapViewport) => void;
  setOperationalPeriod: (n: number) => void;
  appendEvent: (e: AgentEvent) => void;
  clearEvents: () => void;
  upsertInterrupt: (i: PendingInterrupt) => void;
  removeInterrupt: (id?: string) => void;
};

const DEFAULT_VIEWPORT: MapViewport = {
  longitude: -119.5,
  latitude: 37.5,
  zoom: 5.5,
};

export const useStore = create<Store>((set) => ({
  selectedIncidentId: null,
  selectedThreadId: null,
  activeTab: "Operations",
  mapViewport: DEFAULT_VIEWPORT,
  operationalPeriod: 1,
  agentEvents: [],
  pendingInterrupts: [],
  setSelectedIncident: (id) => set({ selectedIncidentId: id }),
  setSelectedThread: (id) => set({ selectedThreadId: id }),
  setActiveTab: (t) => set({ activeTab: t }),
  setViewport: (v) => set({ mapViewport: v }),
  setOperationalPeriod: (n) => set({ operationalPeriod: n }),
  appendEvent: (e) =>
    set((s) => ({ agentEvents: [...s.agentEvents.slice(-499), e] })),
  clearEvents: () => set({ agentEvents: [] }),
  upsertInterrupt: (i) =>
    set((s) => {
      const id = i.interrupt.id;
      const without = s.pendingInterrupts.filter(
        (x) => x.thread_id !== i.thread_id || x.interrupt.id !== id,
      );
      return { pendingInterrupts: [...without, i] };
    }),
  removeInterrupt: (id) =>
    set((s) => ({
      pendingInterrupts: s.pendingInterrupts.filter(
        (x) => x.interrupt.id !== id,
      ),
    })),
}));
