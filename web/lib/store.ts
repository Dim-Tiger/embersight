"use client";

import { create } from "zustand";

export type Tab =
  | "Operations"
  | "Briefing"
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

export type CitationBundle = {
  datasets?: Array<{
    name?: string;
    version?: string | null;
    timestamp?: string | null;
    url?: string | null;
  }>;
  models?: Array<{ name?: string; version?: string | null }>;
  reasoning_trace_id?: string | null;
};

export type AgentOutput = {
  agent: string;
  narrative: string;
  payload: Record<string, unknown>;
  confidence: number;
  confidence_driver?: string;
  citation_bundle?: CitationBundle;
};

export type AgentStatus = "pending" | "running" | "done" | "error";

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

export type ToolCallStatus = "running" | "done" | "error";

export type ToolCall = {
  id: string;             // run_id from backend
  name: string;           // consult_weather_wind etc.
  agentLabel: string;     // "Weather & Wind"
  args?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  status: ToolCallStatus;
  ts: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "agent" | "system";
  text: string;
  ts: number;
  agentName?: string;
  streaming?: boolean;          // true while chat_token deltas accumulate
  toolCalls?: ToolCall[];       // delegations the IC made during this turn
};

export type RunMode = "briefing" | "chat";

export type Store = {
  selectedIncidentId: string | null;
  selectedThreadId: string | null;
  activeTab: Tab;
  mapViewport: MapViewport;
  operationalPeriod: number;
  agentEvents: AgentEvent[];
  agentOutputs: Record<string, AgentOutput>;
  agentStatuses: Record<string, AgentStatus>;
  pendingInterrupts: PendingInterrupt[];
  streaming: boolean;
  done: boolean;
  errorMessage: string | null;
  connectionStatus: "idle" | "posting" | "responded" | "consuming" | "closed";
  chunkCount: number;
  frameCount: number;
  briefingComplete: boolean;
  currentMode: RunMode | null;
  chat: ChatMessage[];
  pendingUserQuery: string | null;
  setSelectedIncident: (id: string | null) => void;
  setSelectedThread: (id: string | null) => void;
  setActiveTab: (t: Tab) => void;
  setViewport: (v: MapViewport) => void;
  setOperationalPeriod: (n: number) => void;
  appendEvent: (e: AgentEvent) => void;
  setAgentOutput: (name: string, output: AgentOutput) => void;
  setAgentStatus: (name: string, status: AgentStatus) => void;
  clearRun: () => void;
  setStreaming: (b: boolean) => void;
  setDone: (b: boolean) => void;
  setError: (m: string | null) => void;
  setConnectionStatus: (s: Store["connectionStatus"]) => void;
  incChunk: () => void;
  incFrame: () => void;
  setBriefingComplete: (b: boolean) => void;
  setCurrentMode: (m: RunMode | null) => void;
  // Chat turn helpers used by the SSE consumer
  beginAgentChat: (id: string) => void;
  appendChatToken: (id: string, delta: string) => void;
  finalizeAgentChat: (id: string) => void;
  attachToolCall: (chatId: string, call: ToolCall) => void;
  resolveToolCall: (
    chatId: string,
    callId: string,
    summary: Record<string, unknown>,
  ) => void;
  appendChat: (m: ChatMessage) => void;
  setPendingUserQuery: (q: string | null) => void;
  upsertInterrupt: (i: PendingInterrupt) => void;
  removeInterrupt: (id?: string) => void;
  restartCount: number;
  requestRestart: () => void;
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
  agentOutputs: {},
  agentStatuses: {},
  pendingInterrupts: [],
  streaming: false,
  done: false,
  errorMessage: null,
  connectionStatus: "idle",
  chunkCount: 0,
  frameCount: 0,
  briefingComplete: false,
  currentMode: null,
  chat: [],
  pendingUserQuery: null,
  restartCount: 0,
  setSelectedIncident: (id) => set({ selectedIncidentId: id }),
  setSelectedThread: (id) => set({ selectedThreadId: id }),
  setActiveTab: (t) => set({ activeTab: t }),
  setViewport: (v) => set({ mapViewport: v }),
  setOperationalPeriod: (n) => set({ operationalPeriod: n }),
  appendEvent: (e) =>
    set((s) => ({ agentEvents: [...s.agentEvents.slice(-499), e] })),
  setAgentOutput: (name, output) =>
    set((s) => ({
      agentOutputs: { ...s.agentOutputs, [name]: output },
      agentStatuses: { ...s.agentStatuses, [name]: "done" },
    })),
  setAgentStatus: (name, status) =>
    set((s) => ({ agentStatuses: { ...s.agentStatuses, [name]: status } })),
  clearRun: () =>
    set({
      agentEvents: [],
      agentOutputs: {},
      agentStatuses: {},
      pendingInterrupts: [],
      streaming: false,
      done: false,
      errorMessage: null,
      connectionStatus: "idle",
      chunkCount: 0,
      frameCount: 0,
      briefingComplete: false,
      currentMode: null,
      chat: [],
    }),
  setStreaming: (b) => set({ streaming: b }),
  setDone: (b) => set({ done: b }),
  setError: (m) => set({ errorMessage: m }),
  setConnectionStatus: (s) => set({ connectionStatus: s }),
  incChunk: () => set((s) => ({ chunkCount: s.chunkCount + 1 })),
  incFrame: () => set((s) => ({ frameCount: s.frameCount + 1 })),
  setBriefingComplete: (b) => set({ briefingComplete: b }),
  setCurrentMode: (m) => set({ currentMode: m }),
  beginAgentChat: (id) =>
    set((s) => ({
      chat: [
        ...s.chat.slice(-199),
        {
          id,
          role: "agent",
          agentName: "Master IC",
          text: "",
          ts: Date.now(),
          streaming: true,
        },
      ],
    })),
  appendChatToken: (id, delta) =>
    set((s) => ({
      chat: s.chat.map((m) =>
        m.id === id ? { ...m, text: (m.text || "") + delta } : m,
      ),
    })),
  finalizeAgentChat: (id) =>
    set((s) => ({
      chat: s.chat.map((m) => (m.id === id ? { ...m, streaming: false } : m)),
    })),
  attachToolCall: (chatId, call) =>
    set((s) => ({
      chat: s.chat.map((m) =>
        m.id === chatId
          ? { ...m, toolCalls: [...(m.toolCalls ?? []), call] }
          : m,
      ),
    })),
  resolveToolCall: (chatId, callId, summary) =>
    set((s) => ({
      chat: s.chat.map((m) =>
        m.id === chatId
          ? {
              ...m,
              toolCalls: (m.toolCalls ?? []).map((c) =>
                c.id === callId
                  ? { ...c, summary, status: "done" as const }
                  : c,
              ),
            }
          : m,
      ),
    })),
  appendChat: (m) => set((s) => ({ chat: [...s.chat.slice(-199), m] })),
  setPendingUserQuery: (q) => set({ pendingUserQuery: q }),
  requestRestart: () => set((s) => ({ restartCount: s.restartCount + 1 })),
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

export const AGENT_ORDER = [
  "orchestrator",
  "weather_wind",
  "terrain_fuel",
  "values_at_risk",
  "routing_staging",
  "spread_simulation",
  "resource_recommendation",
  "evacuation_intelligence",
  "master_ic",
] as const;

export type AgentName = (typeof AGENT_ORDER)[number];

export const AGENT_LABELS: Record<string, string> = {
  orchestrator: "Orchestrator",
  weather_wind: "Weather & Wind",
  terrain_fuel: "Terrain & Fuel",
  values_at_risk: "Values at Risk",
  routing_staging: "Routing & Staging",
  spread_simulation: "Spread Simulation",
  resource_recommendation: "Resources",
  evacuation_intelligence: "Evacuation Intel",
  master_ic: "Master IC",
};
