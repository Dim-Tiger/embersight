"use client";

import { useIncidents } from "@/lib/queries";
import { useStore } from "@/lib/store";
import { Send } from "lucide-react";
import { useState } from "react";
import { useAgentStream } from "./useAgentStream";

export function MessageInput() {
  const [draft, setDraft] = useState("");
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const streaming = useStore((s) => s.streaming);
  const briefingComplete = useStore((s) => s.briefingComplete);
  const { data: incidents } = useIncidents();
  const { sendMessage } = useAgentStream();

  const incident = incidents?.find((i) => i.id === selectedIncidentId) ?? null;
  const disabled =
    !incident || streaming || !briefingComplete || draft.trim().length === 0;

  async function submit() {
    if (!incident || !draft.trim()) return;
    const q = draft.trim();
    setDraft("");
    await sendMessage(incident, q);
  }

  const placeholder = !incident
    ? "Select an incident to begin"
    : !briefingComplete
      ? "Briefing in progress — the IC will be available momentarily…"
      : streaming
        ? "IC is responding…"
        : `Talk to the AI IC about ${incident.name}`;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
      className="flex items-center gap-2 border-t border-smoke-700 bg-smoke-800/60 px-3 py-2"
    >
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={placeholder}
        disabled={!incident || streaming || !briefingComplete}
        className="flex-1 rounded bg-smoke-900 px-3 py-1.5 text-xs text-smoke-200 placeholder:text-smoke-500 focus:outline-none focus:ring-1 focus:ring-ember-500 disabled:opacity-50"
      />
      <button
        type="submit"
        disabled={disabled}
        className="flex items-center gap-1 rounded bg-ember-600 px-2.5 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-ember-500 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <Send className="h-3 w-3" />
        Send
      </button>
    </form>
  );
}
