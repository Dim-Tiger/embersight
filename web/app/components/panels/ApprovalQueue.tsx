"use client";

import { postResume } from "@/lib/queries";
import { useStore } from "@/lib/store";
import { useState } from "react";

const TYPE_LABEL: Record<string, string> = {
  iap_approval: "IAP draft — IC approval required",
  resource_recommendation: "Resource RECOMMENDATION — approval required",
  evac_zone_change: "Evacuation zone change PROPOSED",
  trigger_point_violation: "Trigger-point breach — review required",
};

export function ApprovalQueue() {
  const pending = useStore((s) => s.pendingInterrupts);
  const remove = useStore((s) => s.removeInterrupt);
  const [busy, setBusy] = useState<string | null>(null);

  async function respond(
    threadId: string,
    interruptId: string | undefined,
    decision: "approved" | "edited" | "rejected",
  ) {
    setBusy(interruptId ?? threadId);
    try {
      await postResume(threadId, { decision, actor: "ic@example.com" });
      remove(interruptId);
    } finally {
      setBusy(null);
    }
  }

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
          const id = p.interrupt.id ?? p.thread_id;
          const label =
            TYPE_LABEL[p.interrupt.type] ?? p.interrupt.type;
          const confidence =
            (p.interrupt.payload as any)?.confidence ??
            (p.interrupt.payload as any)?.draft?.confidence;
          return (
            <article
              key={id}
              className="border-b border-smoke-700/70 px-4 py-3"
            >
              <div className="mb-1 flex items-baseline justify-between">
                <h3 className="text-xs font-semibold text-ember-200">
                  {label}
                </h3>
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
                  {JSON.stringify(p.interrupt.payload, null, 2)}
                </pre>
              </details>
              <div className="mt-2 flex gap-2">
                <ActionButton
                  disabled={busy === id}
                  onClick={() => respond(p.thread_id, p.interrupt.id, "approved")}
                  variant="primary"
                >
                  Approve
                </ActionButton>
                <ActionButton
                  disabled={busy === id}
                  onClick={() => respond(p.thread_id, p.interrupt.id, "edited")}
                >
                  Edit
                </ActionButton>
                <ActionButton
                  disabled={busy === id}
                  onClick={() => respond(p.thread_id, p.interrupt.id, "rejected")}
                  variant="danger"
                >
                  Reject
                </ActionButton>
              </div>
            </article>
          );
        })}
      </div>
    </section>
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
