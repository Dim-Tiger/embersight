"use client";

import { postResume } from "@/lib/queries";
import { useStore } from "@/lib/store";
import { FileText } from "lucide-react";
import { useMemo, useState } from "react";
import { Markdown } from "@/app/components/Markdown";
import { AgentActivityBanner } from "./AgentActivityBanner";

type Draft = {
  form?: string;
  operational_period?: number;
  objectives?: string[];
  sections?: Array<{ title?: string; body?: string }>;
  assignments?: Array<{
    division?: string;
    resources?: string[];
    work_assignment?: string;
    special_instructions?: string;
  }>;
  key_findings?: string[];
  safety_message?: string;
  drafted_at?: string;
  synthesis_mode?: string;
  status?: string;
  dissent_log?: Array<{
    agents?: string[];
    agent?: string;
    kind?: string;
    concern?: string;
    rationale?: string;
    confidence?: number;
  }>;
};

export function IAPDraft() {
  const pending = useStore((s) => s.pendingInterrupts);
  const masterOut = useStore((s) => s.agentOutputs.master_ic);
  const remove = useStore((s) => s.removeInterrupt);
  const streamError = useStore((s) => s.errorMessage);

  const view = useMemo(() => {
    const fromInterrupt = pending.find(
      (p) => p.interrupt.type === "iap_approval",
    );
    if (fromInterrupt) {
      const payload =
        (fromInterrupt.interrupt.payload as Record<string, unknown> | undefined) ??
        {};
      const draft = (payload.draft as Draft | undefined) ?? null;
      return {
        draft,
        pending: true,
        threadId: fromInterrupt.thread_id,
        interruptId: fromInterrupt.interrupt.id,
        confidence: (payload.confidence as number | undefined) ?? null,
      } as const;
    }
    const committed = (masterOut?.payload as Record<string, unknown> | undefined)?.[
      "iap_draft"
    ] as Draft | undefined;
    return {
      draft: committed ?? null,
      pending: false,
      threadId: null,
      interruptId: null,
      confidence: masterOut?.confidence ?? null,
    } as const;
  }, [pending, masterOut]);

  const [busy, setBusy] = useState(false);

  async function respond(decision: "approved" | "edited" | "rejected") {
    if (!view.threadId) return;
    setBusy(true);
    try {
      await postResume(view.threadId, { decision, actor: "ic@example.com" });
      remove(view.interruptId ?? undefined);
    } finally {
      setBusy(false);
    }
  }

  const draft = view.draft;

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">
            Incident Action Plan — draft
          </h2>
          <p className="text-xs text-smoke-400">
            ICS 201 → 202 → 204 → 215 → 215A. Every form is{" "}
            <span className="text-ember-200">pending IC approval</span>.
            EmberSight only proposes; the human IC signs.
          </p>
        </header>

        <AgentActivityBanner
          title="Master IC Synthesis"
          subtitle="Synthesises all 7 specialists into ICS forms"
          agents={["orchestrator", "master_ic"]}
          icon={<FileText className="h-5 w-5" />}
        />

        {view.pending && (
          <div className="flex items-center justify-between rounded border border-ember-700/60 bg-ember-900/30 px-3 py-2 text-xs text-ember-200">
            <span>
              ⚠ Pending IC approval
              {view.confidence != null
                ? ` · synthesis confidence ${Math.round(view.confidence * 100)}%`
                : ""}
            </span>
            <div className="flex gap-1.5">
              <ActionButton
                disabled={busy}
                onClick={() => respond("approved")}
                variant="primary"
              >
                Approve
              </ActionButton>
              <ActionButton
                disabled={busy}
                onClick={() => respond("edited")}
              >
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
          </div>
        )}

        {!draft && streamError ? (
          <div className="rounded border border-red-800/60 bg-red-900/20 px-3 py-5 text-xs text-red-200">
            <div className="mb-1 font-medium">Agent service unavailable</div>
            <div className="break-words text-[11px] text-red-300/90">
              {streamError}
            </div>
            <div className="mt-2 text-[11px] text-red-300/70">
              The IAP draft is produced by the Master IC after the seven
              subagents finish. Restart the stream from the Agent activity panel
              once the service is back.
            </div>
          </div>
        ) : !draft ? (
          <div className="rounded border border-dashed border-smoke-700 px-3 py-6 text-center text-xs text-smoke-400">
            No IAP draft yet. Start an agent run by selecting an incident — the
            Master IC drafts the form after the seven subagents finish.
          </div>
        ) : (
          <FormView draft={draft} />
        )}
      </div>
    </div>
  );
}

function FormView({ draft }: { draft: Draft }) {
  return (
    <article className="space-y-4 rounded-md border border-smoke-700 bg-smoke-800/40 p-5">
      <header className="border-b border-smoke-700 pb-3">
        <div className="flex items-baseline justify-between">
          <h3 className="text-lg font-bold text-smoke-100">
            {draft.form ?? "ICS Form"}
          </h3>
          {draft.operational_period != null && (
            <span className="text-[11px] text-smoke-400">
              Operational period {draft.operational_period}
            </span>
          )}
        </div>
        <div className="mt-1 flex gap-3 text-[10px] text-smoke-500">
          {draft.status && <span>status: {draft.status}</span>}
          {draft.synthesis_mode && <span>mode: {draft.synthesis_mode}</span>}
          {draft.drafted_at && (
            <span>drafted {new Date(draft.drafted_at).toLocaleString()}</span>
          )}
        </div>
      </header>

      {draft.objectives && draft.objectives.length > 0 && (
        <section>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-ember-300">
            Objectives
          </h4>
          <ol className="list-decimal space-y-1 pl-5 text-[12px] text-smoke-200">
            {draft.objectives.map((o, i) => (
              <li key={i}>
                <Markdown inline>{o}</Markdown>
              </li>
            ))}
          </ol>
        </section>
      )}

      {draft.safety_message && (
        <section className="rounded border border-amber-700/50 bg-amber-900/20 px-3 py-2">
          <h4 className="text-[10px] font-semibold uppercase tracking-widest text-amber-300">
            Safety message
          </h4>
          <Markdown className="mt-0.5 text-[12px] text-amber-100">
            {draft.safety_message}
          </Markdown>
        </section>
      )}

      {draft.sections && draft.sections.length > 0 && (
        <section className="space-y-3">
          {draft.sections.map((s, i) => (
            <div key={i}>
              <h4 className="mb-0.5 text-[11px] font-semibold uppercase tracking-widest text-smoke-400">
                {s.title}
              </h4>
              {s.body && (
                <Markdown className="text-[12px] leading-relaxed text-smoke-200">
                  {s.body}
                </Markdown>
              )}
            </div>
          ))}
        </section>
      )}

      {draft.assignments && draft.assignments.length > 0 && (
        <section>
          <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-widest text-ember-300">
            Assignments (ICS-204 drafts)
          </h4>
          <div className="space-y-2">
            {draft.assignments.map((a, i) => (
              <div
                key={i}
                className="rounded border border-smoke-700 bg-smoke-900/60 p-3 text-[12px]"
              >
                <div className="font-semibold text-smoke-100">{a.division}</div>
                {a.resources && a.resources.length > 0 && (
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-smoke-300">
                    {a.resources.map((r, j) => (
                      <li key={j}>
                        <Markdown inline>{r}</Markdown>
                      </li>
                    ))}
                  </ul>
                )}
                {a.work_assignment && (
                  <Markdown className="mt-1 text-smoke-300">
                    {a.work_assignment}
                  </Markdown>
                )}
                {a.special_instructions && (
                  <Markdown className="mt-1 text-[11px] italic text-smoke-500">
                    {a.special_instructions}
                  </Markdown>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {draft.key_findings && draft.key_findings.length > 0 && (
        <section>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-widest text-smoke-400">
            Key findings
          </h4>
          <ul className="list-disc space-y-0.5 pl-5 text-[12px] text-smoke-300">
            {draft.key_findings.map((f, i) => (
              <li key={i}>
                <Markdown inline>{f}</Markdown>
              </li>
            ))}
          </ul>
        </section>
      )}

      {draft.dissent_log && draft.dissent_log.length > 0 && (
        <section className="rounded border border-red-700/40 bg-red-900/10 p-3">
          <h4 className="text-[11px] font-semibold uppercase tracking-widest text-red-300">
            Dissent log · {draft.dissent_log.length}
          </h4>
          <ul className="mt-1 space-y-1 text-[11px] text-red-200">
            {draft.dissent_log.map((d, i) => (
              <li key={i}>
                <span className="font-semibold">
                  {d.kind ?? "concern"} ·{" "}
                  {d.agents?.join(", ") || d.agent || "—"}
                </span>
                {d.concern && (
                  <Markdown className="text-smoke-300">{d.concern}</Markdown>
                )}
                {d.rationale && (
                  <Markdown className="text-smoke-500">{d.rationale}</Markdown>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
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
        ? "bg-smoke-800 hover:bg-red-900 text-smoke-200"
        : "bg-smoke-800 hover:bg-smoke-700 text-smoke-200";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded px-2 py-0.5 text-[11px] font-semibold transition-colors disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}
