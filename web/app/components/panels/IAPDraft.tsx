"use client";

import { useStore } from "@/lib/store";
import { useMemo } from "react";

export function IAPDraft() {
  const events = useStore((s) => s.agentEvents);
  const pending = useStore((s) => s.pendingInterrupts);

  const draft = useMemo(() => {
    const fromInterrupt = pending.find(
      (p) => p.interrupt.type === "iap_approval",
    );
    if (fromInterrupt) {
      return {
        draft:
          (fromInterrupt.interrupt.payload as Record<string, unknown> | undefined)?.draft,
        pending: true,
      } as const;
    }
    // Fallback: scan events for the most recent master_ic output
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      const data = e.data as any;
      const iap = data?.data?.output?.iap_draft;
      if (iap) return { draft: iap, pending: false } as const;
    }
    return { draft: null, pending: false } as const;
  }, [pending, events]);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-3xl space-y-4">
        <header>
          <h2 className="text-xl font-semibold text-smoke-200">
            Incident Action Plan — draft
          </h2>
          <p className="text-xs text-smoke-400">
            ICS 201 → 202 → 204 → 215 → 215A. Every form is{" "}
            <span className="text-ember-200">pending IC approval</span> until
            its interrupt is resolved in the approval queue.
          </p>
        </header>

        {draft.pending && (
          <div className="rounded border border-ember-700/60 bg-ember-900/30 px-3 py-2 text-xs text-ember-200">
            ⚠ Pending IC approval — open the Operations tab to approve, edit,
            or reject this draft.
          </div>
        )}

        {!draft.draft ? (
          <div className="rounded border border-dashed border-smoke-700 px-3 py-6 text-center text-xs text-smoke-400">
            No IAP draft yet. Start an agent run by selecting an incident.
          </div>
        ) : (
          <pre className="overflow-auto rounded bg-smoke-800 p-4 text-[11px] leading-relaxed text-smoke-200">
            {JSON.stringify(draft.draft, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
