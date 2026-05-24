"use client";

import { postResume } from "@/lib/queries";
import {
  type EvacZoneChangePayload,
  type PendingInterrupt,
  useStore,
} from "@/lib/store";
import { useState } from "react";
import { useIncidents } from "@/lib/queries";
import { Markdown } from "@/app/components/Markdown";

const TYPE_LABEL: Record<string, string> = {
  iap_approval: "IAP draft — IC approval required",
  resource_recommendation: "Resource RECOMMENDATION — approval required",
  evac_zone_change: "Evacuation zone change PROPOSED",
  trigger_point_violation: "Trigger-point breach — review required",
};

export function ApprovalQueue() {
  const pending = useStore((s) => s.pendingInterrupts);

  return (
    <section className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <h2 className="text-sm font-semibold text-smoke-200">Approval queue</h2>
        <span className="rounded bg-ember-900/60 px-2 py-0.5 text-[10px] uppercase tracking-widest text-ember-200">
          human-in-the-loop
        </span>
      </header>
      <div className="flex-1 overflow-y-auto">
        {pending.length === 0 && (
          <div className="px-4 py-6 text-xs text-smoke-400">
            No pending interrupts. Drafts will surface here when an agent
            requests human approval.
          </div>
        )}
        {pending.map((p) => {
          if (p.interrupt.type === "evac_zone_change") {
            return <EvacZoneCard key={p.interrupt.id ?? p.thread_id} item={p} />;
          }
          return <GenericInterruptCard key={p.interrupt.id ?? p.thread_id} item={p} />;
        })}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------
// Generic fallback (iap_approval / resource_recommendation / etc.)
// --------------------------------------------------------------------------
function GenericInterruptCard({ item }: { item: PendingInterrupt }) {
  const remove = useStore((s) => s.removeInterrupt);
  const [busy, setBusy] = useState(false);
  const id = item.interrupt.id ?? item.thread_id;
  const label = TYPE_LABEL[item.interrupt.type] ?? item.interrupt.type;
  const confidence =
    (item.interrupt.payload as any)?.confidence ??
    (item.interrupt.payload as any)?.draft?.confidence;

  async function respond(decision: "approved" | "edited" | "rejected") {
    setBusy(true);
    try {
      await postResume(item.thread_id, { decision, actor: "ic@example.com" });
      remove(item.interrupt.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="border-b border-smoke-700/70 px-4 py-3">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="text-xs font-semibold text-ember-200">{label}</h3>
        {confidence != null && (
          <span className="text-[11px] text-smoke-400">
            confidence {Math.round(Number(confidence) * 100)}%
          </span>
        )}
      </div>
      <details className="mt-1 text-[11px] text-smoke-200">
        <summary className="cursor-pointer text-smoke-400">
          view draft + citations
        </summary>
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-smoke-800 p-2 text-[10px] leading-tight text-smoke-200">
          {JSON.stringify(item.interrupt.payload, null, 2)}
        </pre>
      </details>
      <div className="mt-2 flex gap-2">
        <ActionButton
          disabled={busy}
          onClick={() => respond("approved")}
          variant="primary"
        >
          Approve
        </ActionButton>
        <ActionButton disabled={busy} onClick={() => respond("edited")}>
          Edit
        </ActionButton>
        <ActionButton
          disabled={busy}
          onClick={() => respond("rejected")}
          variant="danger"
        >
          Reject
        </ActionButton>
      </div>
    </article>
  );
}

// --------------------------------------------------------------------------
// Rich evacuation-zone card — the new headline interaction
// --------------------------------------------------------------------------
function EvacZoneCard({ item }: { item: PendingInterrupt }) {
  const remove = useStore((s) => s.removeInterrupt);
  const acceptEvacZone = useStore((s) => s.acceptEvacZone);
  const toggleRefine = useStore((s) => s.toggleEvacRefine);
  const [busy, setBusy] = useState(false);
  const payload = (item.interrupt.payload ?? {}) as EvacZoneChangePayload;
  const interruptId = item.interrupt.id ?? item.thread_id;

  const proposed = (payload.proposed_status ?? "WARNING").toUpperCase() as
    | "WARNING"
    | "ORDER"
    | "NORMAL";
  const isOrder = proposed === "ORDER";
  const isWarning = proposed === "WARNING";

  const swatch = isOrder
    ? "bg-red-600 text-white"
    : isWarning
      ? "bg-yellow-400 text-black"
      : "bg-smoke-600 text-white";
  const pulseDot = isOrder ? "bg-red-500" : "bg-yellow-300";
  const accentBorder = isOrder
    ? "border-red-700/60"
    : isWarning
      ? "border-yellow-500/60"
      : "border-smoke-700";

  const why: string[] = Array.isArray(payload.why)
    ? payload.why
    : payload.rationale
      ? [payload.rationale]
      : [];

  const impact = payload.impact ?? {};
  const displacement = impact.human_displacement_estimate ?? payload.population_estimate;
  const structures = impact.residential_structures_estimate;

  async function respond(decision: "approved" | "rejected") {
    setBusy(true);
    try {
      await postResume(item.thread_id, { decision, actor: "ic@example.com" });
      if (decision === "approved" && payload.polygon_geojson) {
        acceptEvacZone({
          zone_id: payload.zone_id ?? interruptId,
          name: payload.name ?? "Approved zone",
          status: (proposed === "ORDER" || proposed === "WARNING") ? proposed : "WARNING",
          polygon: payload.polygon_geojson,
          accepted_at: Date.now(),
        });
      }
      remove(item.interrupt.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      className={`border-b border-l-2 ${accentBorder} bg-smoke-900/40 px-4 py-3`}
    >
      {/* Header: pulsing dot + proposed-status pill + zone name */}
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span
              className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${pulseDot}`}
            />
            <span
              className={`relative inline-flex h-2.5 w-2.5 rounded-full ${pulseDot}`}
            />
          </span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-widest ${swatch}`}
          >
            Suggested Evac {proposed}
          </span>
        </div>
        <span className="text-[10px] text-smoke-500">
          {payload.rationale_source === "llm" ? "AI synthesis" : "rule-based"}
        </span>
      </div>

      <h3 className="text-xs font-semibold text-smoke-100">
        {payload.name ?? "Evac zone"}
        {payload.jurisdiction && (
          <span className="ml-1 text-[10px] font-normal text-smoke-400">
            · {payload.jurisdiction}
          </span>
        )}
      </h3>
      {payload.current_status && (
        <p className="text-[10px] text-smoke-400">
          Current: {payload.current_status} → Proposed:{" "}
          <span className={isOrder ? "text-red-400" : "text-yellow-300"}>
            {proposed}
          </span>
        </p>
      )}

      {/* Why */}
      {why.length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] font-semibold uppercase tracking-widest text-ember-300">
            Why
          </div>
          <ul className="mt-1 space-y-0.5 text-[11px] text-smoke-200">
            {why.map((w, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-smoke-500">·</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Impact */}
      <div className="mt-2">
        <div className="text-[10px] font-semibold uppercase tracking-widest text-ember-300">
          Impact
        </div>
        <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px] text-smoke-200">
          <div>
            <span className="text-smoke-400">Human displacement</span>
            <div className="text-sm font-semibold text-white">
              ~{fmtInt(displacement)} people
            </div>
          </div>
          {structures != null && (
            <div>
              <span className="text-smoke-400">Residential structures</span>
              <div className="text-sm font-semibold text-white">
                ~{fmtInt(structures)}
              </div>
            </div>
          )}
          {impact.egress_clear != null && (
            <div className="col-span-2 text-[10px]">
              <span className="text-smoke-400">Egress: </span>
              <span
                className={
                  impact.egress_clear ? "text-emerald-400" : "text-red-400"
                }
              >
                {impact.egress_clear
                  ? "routes clear"
                  : `at risk (${impact.egress_blocked_edges ?? "?"} edges blocked)`}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="mt-3 flex gap-2">
        <ActionButton
          disabled={busy}
          onClick={() => respond("approved")}
          variant="primary"
        >
          Approve
        </ActionButton>
        <ActionButton
          disabled={busy}
          onClick={() => respond("rejected")}
          variant="danger"
        >
          Reject
        </ActionButton>
        <ActionButton
          disabled={busy}
          onClick={() => toggleRefine(interruptId, !payload.refineOpen)}
        >
          {payload.refineOpen ? "Hide refine" : "Refine"}
        </ActionButton>
      </div>

      {payload.refineOpen && (
        <RefineThread item={item} payload={payload} interruptId={interruptId} />
      )}
    </article>
  );
}

// --------------------------------------------------------------------------
// Inline refine chat — talk with the IC about this specific suggestion
// --------------------------------------------------------------------------
function RefineThread({
  item,
  payload,
  interruptId,
}: {
  item: PendingInterrupt;
  payload: EvacZoneChangePayload;
  interruptId: string;
}) {
  const { data: incidents } = useIncidents();
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const operationalPeriod = useStore((s) => s.operationalPeriod);
  const appendMsg = useStore((s) => s.appendEvacRefineMessage);
  const updateMsg = useStore((s) => s.updateEvacRefineMessage);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const thread = payload.refineThread ?? [];
  const incident = incidents?.find((i) => i.id === selectedIncidentId);

  async function submit() {
    const text = draft.trim();
    if (!text || sending || !incident) return;
    setSending(true);
    setDraft("");

    const userId = `u-${Date.now()}`;
    appendMsg(interruptId, {
      id: userId,
      role: "user",
      text,
      ts: Date.now(),
    });
    const agentId = `a-${Date.now()}`;
    appendMsg(interruptId, {
      id: agentId,
      role: "agent",
      text: "",
      streaming: true,
      ts: Date.now(),
    });

    // Build a context-rich message so the IC understands which suggestion
    // we're talking about even without dedicated state plumbing.
    const contextual = [
      `[Refining proposed evac zone "${payload.name}" (ID ${payload.zone_id})]`,
      `Current status: ${payload.current_status}; proposed: ${payload.proposed_status}.`,
      `Stated rationale: ${payload.rationale ?? "(none)"}`,
      `Estimated human displacement: ${payload.impact?.human_displacement_estimate ?? payload.population_estimate ?? "?"}`,
      "",
      `User says: ${text}`,
    ].join("\n");

    try {
      const res = await fetch("/api/agent/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          incident,
          mode: "chat",
          message: contextual,
          operational_period: operationalPeriod,
          thread_id: item.thread_id,
        }),
      });
      if (!res.ok || !res.body) {
        updateMsg(interruptId, agentId, "(failed to reach the IC)", true);
        return;
      }
      // Custom SSE reader: extract only chat_token deltas, route them into
      // the per-interrupt refine thread. Other events are ignored so the
      // main IC chat doesn't get noise from this side conversation.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const boundary = /\r?\n\r?\n/;
        let m: RegExpExecArray | null;
        while ((m = boundary.exec(buffer)) !== null) {
          const frame = buffer.slice(0, m.index);
          buffer = buffer.slice(m.index + m[0].length);
          let event = "message";
          const dataLines: string[] = [];
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:"))
              event = line.slice(6).trim();
            else if (line.startsWith("data:"))
              dataLines.push(line.slice(5).trim());
          }
          if (event === "chat_token" && dataLines.length) {
            try {
              const p = JSON.parse(dataLines.join("\n"));
              const delta = (p?.delta as string) ?? "";
              if (delta) updateMsg(interruptId, agentId, delta);
            } catch {
              /* skip malformed */
            }
          }
        }
      }
      updateMsg(interruptId, agentId, "", true);
    } catch (err) {
      updateMsg(
        interruptId,
        agentId,
        `\n(error: ${err instanceof Error ? err.message : String(err)})`,
        true,
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mt-3 rounded border border-smoke-700 bg-smoke-900/60 p-2">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-ember-300">
        Refine with the IC
      </div>
      <div className="mb-2 max-h-40 space-y-1.5 overflow-y-auto">
        {thread.length === 0 && (
          <div className="text-[10px] italic text-smoke-500">
            Ask the IC to adjust this suggestion — shrink the zone, change the
            status, exclude a school, etc.
          </div>
        )}
        {thread.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[88%] rounded px-2 py-1 text-[11px] leading-snug ${
                m.role === "user"
                  ? "bg-ember-600/80 text-white"
                  : "bg-smoke-800 text-smoke-200 ring-1 ring-smoke-700"
              }`}
            >
              {m.role === "user" ? (
                <span className="whitespace-pre-wrap">
                  {m.text || (m.streaming ? "…" : "")}
                </span>
              ) : m.text ? (
                <Markdown>{m.text}</Markdown>
              ) : m.streaming ? (
                <span>…</span>
              ) : null}
              {m.streaming && (
                <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-ember-300 align-middle" />
              )}
            </div>
          </div>
        ))}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="flex items-center gap-1"
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            incident
              ? "e.g. exclude the school district to the south"
              : "Select an incident first"
          }
          disabled={!incident || sending}
          className="flex-1 rounded bg-smoke-900 px-2 py-1 text-[11px] text-smoke-200 placeholder:text-smoke-500 focus:outline-none focus:ring-1 focus:ring-ember-500 disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!draft.trim() || sending || !incident}
          className="rounded bg-ember-600 px-2 py-1 text-[10px] font-semibold text-white hover:bg-ember-500 disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}

function ActionButton({
  children,
  onClick,
  disabled,
  variant = "default",
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  variant?: "default" | "primary" | "danger";
}) {
  const styles =
    variant === "primary"
      ? "bg-ember-600 hover:bg-ember-500 text-white"
      : variant === "danger"
        ? "bg-smoke-700 hover:bg-red-900 text-smoke-200"
        : "bg-smoke-700 hover:bg-smoke-600 text-smoke-200";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded px-2 py-1 text-[11px] font-semibold transition-colors disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}

function fmtInt(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}
