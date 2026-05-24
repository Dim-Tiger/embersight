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

export type ChatMessage = {
  id: string;
  role: "user" | "agent";
  text: string;
  ts: number;
  agentName?: string;
};

/**
 * Inter-agent dialogue message. Distinct from `ChatMessage` (which is the
 * user-facing narrative chat) — these surface the actual exchange between
 * the orchestrator and each subagent: the request issued to the agent,
 * any live "thinking" tokens streamed while it runs, and the final
 * response with its narrative + confidence.
 */
export type DialogueMessage = {
  id: string;
  from: string; // "orchestrator" or an agent slug
  to: string; // "orchestrator", "team", or an agent slug
  text: string;
  ts: number;
  kind: "request" | "response" | "thinking" | "kickoff";
  confidence?: number | null;
  confidenceDriver?: string | null;
};

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
  chat: ChatMessage[];
  dialogue: DialogueMessage[];
  /** Live, in-flight LLM-token buffer keyed by langgraph_node name.
   * Cleared when the agent's `response` dialogue message arrives. */
  thinking: Record<string, string>;
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
  appendChat: (m: ChatMessage) => void;
  appendDialogue: (d: DialogueMessage) => void;
  appendThinking: (agent: string, chunk: string) => void;
  clearThinking: (agent: string) => void;
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
  chat: [],
  dialogue: [],
  thinking: {},
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
      dialogue: [],
      thinking: {},
      streaming: false,
      done: false,
      errorMessage: null,
      connectionStatus: "idle",
      chunkCount: 0,
      frameCount: 0,
    }),
  setStreaming: (b) => set({ streaming: b }),
  setDone: (b) => set({ done: b }),
  setError: (m) => set({ errorMessage: m }),
  setConnectionStatus: (s) => set({ connectionStatus: s }),
  incChunk: () => set((s) => ({ chunkCount: s.chunkCount + 1 })),
  incFrame: () => set((s) => ({ frameCount: s.frameCount + 1 })),
  appendChat: (m) => set((s) => ({ chat: [...s.chat.slice(-199), m] })),
  appendDialogue: (d) =>
    set((s) => ({ dialogue: [...s.dialogue.slice(-299), d] })),
  appendThinking: (agent, chunk) =>
    set((s) => ({
      thinking: {
        ...s.thinking,
        [agent]: (s.thinking[agent] ?? "") + chunk,
      },
    })),
  clearThinking: (agent) =>
    set((s) => {
      if (!(agent in s.thinking)) return s;
      const next = { ...s.thinking };
      delete next[agent];
      return { thinking: next };
    }),
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
