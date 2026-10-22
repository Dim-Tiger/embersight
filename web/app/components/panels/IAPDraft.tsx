"use client";

import { useStore } from "@/lib/store";
import { FileText } from "lucide-react";
import { useMemo } from "react";
import { Markdown } from "@/app/components/Markdown";
import { AgentActivityBanner } from "./AgentActivityBanner";
import { DecisionControls } from "./ApprovalQueue";

// The Master IC sends `sections` as an object keyed by ICS field
// (incident_name, current_situation, planned_actions, current_org_chart...
// or ics_202/ics_203/ics_204... for the op-period-2 bundle). The array form
// is kept for older drafts.
type Draft = {
  form?: string;
  operational_period?: number;
  objectives?: string[];
  sections?: Array<{ title?: string; body?: string }> | Record<string, unknown>;
  dissent_log_acknowledgment?: string[];
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

/** One row of the Master IC's dissent log (interrupt payload / output). */
export type DissentEntry = {
  source_agent?: string;
  conflicting_agent?: string | null;
  claim_a?: string;
  claim_b?: string;
  severity?: "low" | "medium" | "high" | string;
  rationale?: string;
};

export function IAPDraft() {
  const pending = useStore((s) => s.pendingInterrupts);
  const masterOut = useStore((s) => s.agentOutputs.master_ic);
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
        dissent: (payload.dissent_log as DissentEntry[] | undefined) ?? [],
        pending: true,
        item: fromInterrupt,
        confidence: (payload.confidence as number | undefined) ?? null,
      } as const;
    }
    const out = (masterOut?.payload ?? {}) as Record<string, unknown>;
    const committed = out.iap_draft as Draft | undefined;
    return {
      // A rejected draft comes back as {}: treat it as "no draft".
      draft: committed && Object.keys(committed).length ? committed : null,
      dissent: (out.dissent_log as DissentEntry[] | undefined) ?? [],
      pending: false,
      item: null,
      confidence: masterOut?.confidence ?? null,
    } as const;
  }, [pending, masterOut]);

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

        {view.pending && view.item && (
          <div className="rounded border border-ember-700/60 bg-ember-900/20 px-3 py-2">
            <div className="text-xs font-semibold text-ember-200">
              DRAFT pending your approval
              {view.confidence != null
                ? ` · synthesis confidence ${Math.round(view.confidence * 100)}%`
                : ""}
            </div>
            {/* The whole form is on screen here, so it counts as reviewed. */}
            <DecisionControls item={view.item} reviewed />
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
          <FormView draft={draft} dissent={view.dissent} />
        )}
      </div>
    </div>
  );
}

function FormView({
  draft,
  dissent,
}: {
  draft: Draft;
  dissent: DissentEntry[];
}) {
  const sectionList = Array.isArray(draft.sections) ? draft.sections : null;
  const sectionMap =
    draft.sections && !Array.isArray(draft.sections) ? draft.sections : null;
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
        <div className="mt-1 flex gap-3 text-[10px] text-smoke-400">
          {draft.status && <span>status: {draft.status}</span>}
          {draft.synthesis_mode && <span>mode: {draft.synthesis_mode}</span>}
          {draft.drafted_at && (
            <span>drafted {new Date(draft.drafted_at).toLocaleString()}</span>
          )}
        </div>
      </header>

      <DissentLog entries={dissent} />

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

      {sectionMap && (
        <section className="space-y-3">
          {Object.entries(sectionMap).map(([key, value]) => (
            <div key={key}>
              <h4 className="mb-0.5 text-[11px] font-semibold uppercase tracking-widest text-smoke-400">
                {humanize(key)}
              </h4>
              <SectionValue value={value} />
            </div>
          ))}
        </section>
      )}

      {sectionList && sectionList.length > 0 && (
        <section className="space-y-3">
          {sectionList.map((s, i) => (
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
                  <Markdown className="mt-1 text-[11px] italic text-smoke-400">
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
                  <Markdown className="text-smoke-400">{d.rationale}</Markdown>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

const SEVERITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

/**
 * The Master IC's own list of gaps and low-confidence inputs. It is the
 * evidence the IC most needs before signing, so it renders above the form
 * body, high severity first.
 */
export function DissentLog({ entries }: { entries: DissentEntry[] }) {
  if (!entries.length) return null;
  const sorted = [...entries].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity ?? ""] ?? 3) -
      (SEVERITY_RANK[b.severity ?? ""] ?? 3),
  );
  return (
    <section className="rounded border border-red-700/40 bg-red-900/10 p-3">
      <h4 className="text-[11px] font-semibold uppercase tracking-widest text-red-300">
        Dissent log · {entries.length} · read before signing
      </h4>
      <ul className="mt-1.5 space-y-1.5 text-[12px]">
        {sorted.map((d, i) => (
          <li key={i} className="flex gap-2">
            <span
              className={`mt-0.5 h-fit flex-shrink-0 rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-wider ${
                d.severity === "high"
                  ? "bg-red-700 text-white"
                  : d.severity === "medium"
                    ? "bg-amber-600 text-white"
                    : "bg-smoke-700 text-smoke-200"
              }`}
            >
              {d.severity ?? "note"}
            </span>
            <div className="min-w-0">
              <span className="font-semibold text-smoke-200">
                {humanize(d.source_agent ?? "agent")}
              </span>
              {d.claim_a && (
                <span className="text-smoke-300"> · {d.claim_a}</span>
              )}
              {d.rationale && (
                <div className="text-[11px] text-smoke-400">{d.rationale}</div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** "current_org_chart" → "Current org chart"; "ics_204" → "ICS 204". */
function humanize(key: string): string {
  const s = key.replace(/_/g, " ").trim();
  if (/^ics \d/i.test(s)) return s.toUpperCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Renders one ICS section value: text, list, or nested fields. */
function SectionValue({ value }: { value: unknown }) {
  if (value == null || value === "") {
    return <span className="text-[12px] italic text-smoke-400">—</span>;
  }
  if (typeof value === "string" || typeof value === "number") {
    return (
      <Markdown className="text-[12px] leading-relaxed text-smoke-200">
        {String(value)}
      </Markdown>
    );
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="text-[12px] italic text-smoke-400">none</span>;
    }
    return (
      <ul className="list-disc space-y-0.5 pl-5 text-[12px] text-smoke-200">
        {value.map((v, i) => (
          <li key={i}>
            {typeof v === "object" && v !== null ? (
              <SectionValue value={v} />
            ) : (
              <Markdown inline>{String(v)}</Markdown>
            )}
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-3 gap-y-0.5 text-[12px]">
        {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-smoke-400">{humanize(k)}</dt>
            <dd className="min-w-0 text-smoke-200">
              <SectionValue value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span className="text-[12px] text-smoke-200">{String(value)}</span>;
}
