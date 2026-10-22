"use client";

import { Markdown } from "@/app/components/Markdown";
import { postResume, useIncidents } from "@/lib/queries";
import {
  type EvacZoneChangePayload,
  type PendingInterrupt,
  useStore,
} from "@/lib/store";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Pencil,
  ShieldCheck,
  X,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

// ==========================================================================
// Approval queue: the one place the incident commander acts.
//
// Every card answers, top to bottom:
//   1. What kind of decision is this and how urgent?   (chip + age/expiry)
//   2. What exactly am I approving?                     (title)
//   3. What is at stake?                                (stake numbers)
//   4. How sure is the system, and why?                 (confidence meter)
//   5. What is the evidence?                            (expandable)
//   6. Approve / Edit / Reject                          (three distinct buttons)
//
// EmberSight never dispatches anything. Approving only records the IC's
// decision and lets the paused agent continue; the copy says so.
// ==========================================================================

/** Below this, Approve stays locked until the IC has opened the evidence. */
const LOW_CONFIDENCE = 0.5;

const ACTOR = "ic@example.com";

type Tone = "red" | "amber" | "ember" | "smoke";

type Stake = { label: string; value: string; tone?: Tone };

type Described = {
  /** Short category label for the chip, e.g. "IAP DRAFT · ICS-201". */
  kind: string;
  tone: Tone;
  /** Lower sorts first. */
  priority: number;
  /** One line: what the IC is being asked to approve. */
  title: string;
  subtitle?: string;
  stakes: Stake[];
  confidence: number | null;
  confidenceNote?: string;
  /** Where the proposal came from (deterministic rules, AI, synthetic test). */
  source?: string;
  evidence: ReactNode;
  evidenceCount: number;
  /** Label for the primary button, keeping the RECOMMEND / PROPOSED / DRAFT wording. */
  approveLabel: string;
  editor: "iap" | "resources" | "evac" | null;
  expiresAt?: string;
};

export function ApprovalQueue() {
  const pending = useStore((s) => s.pendingInterrupts);
  const selectedIncidentId = useStore((s) => s.selectedIncidentId);
  const streaming = useStore((s) => s.streaming);

  // Most urgent first: life-safety proposals before paperwork.
  const sorted = useMemo(
    () =>
      pending
        .map((p) => ({ item: p, d: describe(p) }))
        .sort(
          (a, b) =>
            a.d.priority - b.d.priority ||
            String(a.item.interrupt.created_at ?? "").localeCompare(
              String(b.item.interrupt.created_at ?? ""),
            ),
        ),
    [pending],
  );

  return (
    <section className="flex h-full flex-col" aria-label="Approval queue">
      <header className="border-b border-smoke-700 bg-smoke-800/60 px-4 py-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-smoke-200">
            Approval queue
            {pending.length > 0 && (
              <span className="flex items-center gap-1 rounded-full bg-amber-500 px-2 py-px text-[10px] font-bold text-black">
                {pending.length} waiting
              </span>
            )}
          </h2>
          <span className="flex items-center gap-1 text-[10px] text-smoke-400">
            <ShieldCheck className="h-3 w-3" />
            never dispatches
          </span>
        </div>
        {pending.length > 1 && (
          <p className="text-[10px] text-smoke-400">Most urgent first.</p>
        )}
      </header>
      <div className="flex-1 overflow-y-auto">
        {pending.length === 0 && (
          <div className="px-4 py-5 text-xs leading-relaxed text-smoke-300">
            {!selectedIncidentId
              ? "Pick an incident. Drafts that need your decision will appear here."
              : streaming
                ? "Agents are working. Anything that needs your approval will appear here."
                : "Nothing is waiting for you. Agents pause and post here whenever a DRAFT, RECOMMENDATION or PROPOSED change needs a human decision."}
          </div>
        )}
        {sorted.map(({ item, d }) => (
          <InterruptCard
            key={`${item.thread_id}:${item.interrupt.id ?? item.interrupt.type}`}
            item={item}
            d={d}
          />
        ))}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------
// Card
// --------------------------------------------------------------------------

function InterruptCard({ item, d }: { item: PendingInterrupt; d: Described }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const isEvac = item.interrupt.type === "evac_zone_change";
  const payload = (item.interrupt.payload ?? {}) as EvacZoneChangePayload;
  const toggleRefine = useStore((s) => s.toggleEvacRefine);
  const interruptId = item.interrupt.id ?? item.thread_id;

  function toggleEvidence() {
    setEvidenceOpen((v) => !v);
    setReviewed(true);
  }

  return (
    <article
      className={`border-b border-l-4 border-b-smoke-700 px-4 py-3 ${TONE_BORDER[d.tone]}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${TONE_CHIP[d.tone]}`}
        >
          {d.kind}
        </span>
        <Timing createdAt={item.interrupt.created_at} expiresAt={d.expiresAt} />
      </div>

      <h3 className="mt-1.5 text-[13px] font-semibold leading-snug text-smoke-100">
        {d.title}
      </h3>
      {d.subtitle && (
        <p className="text-[11px] leading-snug text-smoke-300">{d.subtitle}</p>
      )}

      {d.stakes.length > 0 && (
        <dl className="mt-2 grid grid-cols-2 gap-1.5">
          {d.stakes.map((s) => (
            <div
              key={s.label}
              className="rounded border border-smoke-700 bg-smoke-900/60 px-2 py-1"
            >
              <dt className="text-[9px] font-semibold uppercase tracking-wider text-smoke-400">
                {s.label}
              </dt>
              <dd
                className={`text-[13px] font-semibold ${s.tone ? TONE_TEXT[s.tone] : "text-smoke-100"}`}
              >
                {s.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <ConfidenceMeter
        value={d.confidence}
        note={d.confidenceNote}
        source={d.source}
      />

      <button
        type="button"
        onClick={toggleEvidence}
        aria-expanded={evidenceOpen}
        className="mt-2 flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-[11px] font-semibold text-ember-300 hover:bg-smoke-800"
      >
        {evidenceOpen ? (
          <ChevronDown className="h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" />
        )}
        Evidence &amp; citations
        {d.evidenceCount > 0 && (
          <span className="font-normal text-smoke-400">({d.evidenceCount})</span>
        )}
      </button>
      {evidenceOpen && (
        <div className="mt-1 space-y-2 rounded border border-smoke-700 bg-smoke-900/60 p-2 text-[11px] text-smoke-200">
          {d.evidence}
          <details>
            <summary className="cursor-pointer text-[10px] text-smoke-400">
              Raw payload (JSON)
            </summary>
            <pre className="mt-1 max-h-48 overflow-auto rounded bg-smoke-800 p-2 text-[10px] leading-tight text-smoke-300">
              {JSON.stringify(stripUiFields(item.interrupt.payload), null, 2)}
            </pre>
          </details>
        </div>
      )}

      <DecisionControls
        item={item}
        d={d}
        reviewed={reviewed}
        onNeedReview={() => {
          setEvidenceOpen(true);
          setReviewed(true);
        }}
        extra={
          isEvac ? (
            <button
              type="button"
              onClick={() => toggleRefine(interruptId, !payload.refineOpen)}
              className="text-[11px] font-medium text-smoke-300 underline-offset-2 hover:text-smoke-100 hover:underline"
            >
              {payload.refineOpen ? "Hide discussion" : "Discuss with the IC agent"}
            </button>
          ) : null
        }
      />

      {isEvac && payload.refineOpen && (
        <RefineThread item={item} payload={payload} interruptId={interruptId} />
      )}
    </article>
  );
}

// --------------------------------------------------------------------------
// Approve / Edit / Reject. Exported so the IAP tab uses the same controls.
// --------------------------------------------------------------------------

export function DecisionControls({
  item,
  d: given,
  reviewed,
  onNeedReview,
  extra,
}: {
  item: PendingInterrupt;
  d?: Described;
  /** True once the IC has looked at the evidence (or is on a page showing it). */
  reviewed: boolean;
  onNeedReview?: () => void;
  extra?: ReactNode;
}) {
  const d = given ?? describe(item);
  const remove = useStore((s) => s.removeInterrupt);
  const acceptEvacZone = useStore((s) => s.acceptEvacZone);
  const [mode, setMode] = useState<"idle" | "edit" | "reject">("idle");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const lowConfidence = d.confidence != null && d.confidence < LOW_CONFIDENCE;
  const approveLocked = lowConfidence && !reviewed;

  async function send(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await postResume(item.thread_id, { ...body, actor: ACTOR });
      afterDecision(item, body, acceptEvacZone);
      remove(item.interrupt.id, item.thread_id);
    } catch (err) {
      // Keep the card: the decision did not reach the agent service.
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2">
      {lowConfidence && (
        <p className="mb-1.5 flex items-start gap-1.5 rounded border border-amber-600/50 bg-amber-900/20 px-2 py-1 text-[11px] leading-snug text-amber-100">
          <AlertTriangle className="mt-px h-3.5 w-3.5 flex-shrink-0 text-amber-300" />
          <span>
            Low confidence ({Math.round((d.confidence ?? 0) * 100)}%).{" "}
            {approveLocked
              ? "Open the evidence before approving."
              : "Check the evidence; Edit or Reject if it does not hold up."}
          </span>
        </p>
      )}

      {mode === "idle" && (
        <div className="grid grid-cols-[1.6fr_1fr_1fr] gap-1.5">
          <button
            type="button"
            disabled={busy}
            onClick={() => (approveLocked ? onNeedReview?.() : send({ decision: "approved" }))}
            title={approveLocked ? "Low confidence: review the evidence first" : d.approveLabel}
            className={`flex h-8 items-center justify-center gap-1 whitespace-nowrap rounded px-1 text-[12px] font-semibold transition-colors disabled:opacity-50 ${
              approveLocked
                ? "border border-ember-600 bg-transparent text-ember-200 hover:bg-ember-900/40"
                : "bg-ember-600 text-white hover:bg-ember-500"
            }`}
          >
            <Check className="h-3.5 w-3.5" />
            {approveLocked ? "Review to approve" : d.approveLabel}
          </button>
          <button
            type="button"
            disabled={busy || !d.editor}
            onClick={() => setMode("edit")}
            title={d.editor ? "Change the proposal, then approve your version" : "This proposal type cannot be edited here; reject it with a reason instead"}
            className="flex h-8 items-center justify-center gap-1 rounded border border-smoke-500 text-[12px] font-semibold text-smoke-200 transition-colors hover:bg-smoke-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Pencil className="h-3.5 w-3.5" />
            Edit
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setMode("reject")}
            className="flex h-8 items-center justify-center gap-1 rounded border border-red-500/70 text-[12px] font-semibold text-red-300 transition-colors hover:bg-red-900/40"
          >
            <X className="h-3.5 w-3.5" />
            Reject
          </button>
        </div>
      )}

      {mode === "reject" && (
        <form
          className="space-y-1.5 rounded border border-red-700/50 bg-red-900/10 p-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send({ decision: "rejected", reason: reason.trim() || undefined });
          }}
        >
          <label className="block text-[11px] font-semibold text-red-200">
            Reject: reason for the audit log (optional)
            <input
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. engines already committed on the north flank"
              className="mt-1 w-full rounded bg-smoke-900 px-2 py-1 text-[11px] font-normal text-smoke-200 placeholder:text-smoke-400 focus:outline-none focus:ring-1 focus:ring-red-500"
            />
          </label>
          <div className="flex gap-1.5">
            <button
              type="submit"
              disabled={busy}
              className="h-7 rounded bg-red-700 px-3 text-[11px] font-semibold text-white hover:bg-red-600 disabled:opacity-50"
            >
              Confirm reject
            </button>
            <button
              type="button"
              onClick={() => setMode("idle")}
              className="h-7 rounded px-2 text-[11px] text-smoke-300 hover:bg-smoke-700"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode === "edit" && d.editor === "iap" && (
        <IapEditor
          item={item}
          busy={busy}
          onCancel={() => setMode("idle")}
          onSubmit={(edited_draft) => send({ decision: "edited", edited_draft })}
        />
      )}
      {mode === "edit" && d.editor === "resources" && (
        <ResourceEditor
          item={item}
          busy={busy}
          onCancel={() => setMode("idle")}
          onSubmit={(edits) => send({ decision: "edited", edits })}
        />
      )}
      {mode === "edit" && d.editor === "evac" && (
        <EvacEditor
          item={item}
          busy={busy}
          onCancel={() => setMode("idle")}
          onSubmit={(edits) => send({ decision: "edited", edits })}
        />
      )}

      {error && (
        <p role="alert" className="mt-1.5 text-[11px] text-red-300">
          {error}. Nothing was approved or rejected; try again.
        </p>
      )}

      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="text-[10px] text-smoke-400">
          Approving records your decision; nothing is dispatched.
        </span>
        {extra}
      </div>
    </div>
  );
}

/** Side effects after the agent accepted the decision. */
function afterDecision(
  item: PendingInterrupt,
  body: Record<string, unknown>,
  acceptEvacZone: ReturnType<typeof useStore.getState>["acceptEvacZone"],
) {
  if (item.interrupt.type !== "evac_zone_change") return;
  const payload = (item.interrupt.payload ?? {}) as EvacZoneChangePayload;
  const decision = body.decision;
  if (decision !== "approved" && decision !== "edited") return;
  const edited = (body.edits as { proposed_status?: string } | undefined)
    ?.proposed_status;
  const status = String(edited ?? payload.proposed_status ?? "").toUpperCase();
  // Only WARNING / ORDER are drawn as accepted zones; NORMAL clears nothing.
  if ((status === "ORDER" || status === "WARNING") && payload.polygon_geojson) {
    acceptEvacZone({
      zone_id: payload.zone_id ?? item.interrupt.id ?? item.thread_id,
      name: payload.name ?? "Approved zone",
      status,
      polygon: payload.polygon_geojson,
      accepted_at: Date.now(),
    });
  }
}

// --------------------------------------------------------------------------
// Editors: each sends the shape its agent reads on "edited".
//   master_ic        → decision.edited_draft
//   resource_rec     → decision.edits = {apparatus, crews, aircraft, overhead}
//   evac_intel       → decision.edits = {proposed_status}
// --------------------------------------------------------------------------

function IapEditor({
  item,
  busy,
  onCancel,
  onSubmit,
}: {
  item: PendingInterrupt;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (draft: Record<string, unknown>) => void;
}) {
  const draft = ((item.interrupt.payload as Record<string, unknown>)?.draft ??
    {}) as Record<string, unknown>;
  const [text, setText] = useState(
    ((draft.objectives as string[] | undefined) ?? []).join("\n"),
  );
  return (
    <EditorShell
      title="Edit the DRAFT objectives (one per line)"
      busy={busy}
      onCancel={onCancel}
      onSubmit={() =>
        onSubmit({
          ...draft,
          objectives: text
            .split("\n")
            .map((l) => l.trim())
            .filter(Boolean),
        })
      }
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        className="w-full rounded bg-smoke-900 px-2 py-1 text-[11px] leading-snug text-smoke-200 focus:outline-none focus:ring-1 focus:ring-ember-500"
      />
    </EditorShell>
  );
}

type ResourceLine = {
  kind?: string;
  type?: string;
  quantity?: number;
  arrival_window?: string;
  rationale?: string;
};
const RESOURCE_GROUPS = ["apparatus", "crews", "aircraft", "overhead"] as const;

function ResourceEditor({
  item,
  busy,
  onCancel,
  onSubmit,
}: {
  item: PendingInterrupt;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (edits: Record<string, ResourceLine[]>) => void;
}) {
  const rec = ((item.interrupt.payload as Record<string, unknown>)
    ?.recommendation ?? {}) as Record<string, ResourceLine[] | undefined>;
  const [groups, setGroups] = useState(() =>
    Object.fromEntries(
      RESOURCE_GROUPS.map((g) => [g, (rec[g] ?? []).map((l) => ({ ...l }))]),
    ) as Record<(typeof RESOURCE_GROUPS)[number], ResourceLine[]>,
  );
  return (
    <EditorShell
      title="Edit the RECOMMENDED quantities"
      busy={busy}
      onCancel={onCancel}
      onSubmit={() => onSubmit(groups)}
    >
      <div className="max-h-48 space-y-0.5 overflow-y-auto">
        {RESOURCE_GROUPS.flatMap((g) =>
          groups[g].map((line, i) => (
            <label
              key={`${g}-${i}`}
              className="flex items-center justify-between gap-2 text-[11px] text-smoke-200"
            >
              <span className="min-w-0 truncate">{line.type}</span>
              <input
                type="number"
                min={0}
                value={line.quantity ?? 0}
                onChange={(e) => {
                  const q = Math.max(0, Math.round(Number(e.target.value) || 0));
                  setGroups((prev) => ({
                    ...prev,
                    [g]: prev[g].map((l, j) => (j === i ? { ...l, quantity: q } : l)),
                  }));
                }}
                aria-label={`Quantity of ${line.type}`}
                className="w-14 rounded bg-smoke-900 px-1.5 py-0.5 text-right text-[11px] text-smoke-100 focus:outline-none focus:ring-1 focus:ring-ember-500"
              />
            </label>
          )),
        )}
      </div>
    </EditorShell>
  );
}

function EvacEditor({
  item,
  busy,
  onCancel,
  onSubmit,
}: {
  item: PendingInterrupt;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (edits: { proposed_status: string }) => void;
}) {
  const payload = (item.interrupt.payload ?? {}) as EvacZoneChangePayload;
  const [status, setStatus] = useState<string>(
    String(payload.proposed_status ?? "WARNING").toUpperCase(),
  );
  return (
    <EditorShell
      title="Change the PROPOSED status"
      busy={busy}
      onCancel={onCancel}
      onSubmit={() => onSubmit({ proposed_status: status })}
    >
      <select
        value={status}
        onChange={(e) => setStatus(e.target.value)}
        className="w-full rounded bg-smoke-900 px-2 py-1 text-[11px] text-smoke-200 focus:outline-none focus:ring-1 focus:ring-ember-500"
      >
        <option value="ORDER">Evacuation ORDER</option>
        <option value="WARNING">Evacuation WARNING</option>
        <option value="NORMAL">NORMAL (no change)</option>
      </select>
    </EditorShell>
  );
}

function EditorShell({
  title,
  busy,
  onCancel,
  onSubmit,
  children,
}: {
  title: string;
  busy: boolean;
  onCancel: () => void;
  onSubmit: () => void;
  children: ReactNode;
}) {
  return (
    <form
      className="space-y-1.5 rounded border border-smoke-600 bg-smoke-900/60 p-2"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <div className="text-[11px] font-semibold text-smoke-200">{title}</div>
      {children}
      <div className="flex gap-1.5">
        <button
          type="submit"
          disabled={busy}
          className="h-7 rounded bg-ember-600 px-3 text-[11px] font-semibold text-white hover:bg-ember-500 disabled:opacity-50"
        >
          Approve with my edits
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="h-7 rounded px-2 text-[11px] text-smoke-300 hover:bg-smoke-700"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------
// Small pieces
// --------------------------------------------------------------------------

function ConfidenceMeter({
  value,
  note,
  source,
}: {
  value: number | null;
  note?: string;
  source?: string;
}) {
  if (value == null) {
    return source ? (
      <p className="mt-2 text-[10px] text-smoke-400">Source: {source}</p>
    ) : null;
  }
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const band =
    value < LOW_CONFIDENCE ? "Low" : value < 0.75 ? "Medium" : "High";
  const bar =
    value < LOW_CONFIDENCE
      ? "bg-red-500"
      : value < 0.75
        ? "bg-amber-400"
        : "bg-emerald-500";
  return (
    <div className="mt-2">
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="font-semibold text-smoke-200">
          {band} confidence · {pct}%
        </span>
        {source && <span className="text-[10px] text-smoke-400">{source}</span>}
      </div>
      <div
        className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-smoke-700"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Agent confidence"
      >
        <div className={`h-full ${bar}`} style={{ width: `${Math.max(pct, 2)}%` }} />
      </div>
      {note && (
        <p className="mt-0.5 text-[10px] leading-snug text-smoke-400">{note}</p>
      )}
    </div>
  );
}

function Timing({
  createdAt,
  expiresAt,
}: {
  createdAt?: string;
  expiresAt?: string;
}) {
  // Re-render every 30 s so "2 min ago" and "expires in" stay current.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  const parts: string[] = [];
  const created = createdAt ? Date.parse(createdAt) : NaN;
  if (Number.isFinite(created)) parts.push(`${fmtSpan(now - created)} ago`);
  const expires = expiresAt ? Date.parse(expiresAt) : NaN;
  if (Number.isFinite(expires)) {
    parts.push(
      expires > now ? `expires in ${fmtSpan(expires - now)}` : "expired",
    );
  }
  if (!parts.length) return null;
  return (
    <span className="whitespace-nowrap text-[10px] text-smoke-400">
      {parts.join(" · ")}
    </span>
  );
}

function fmtSpan(ms: number): string {
  const min = Math.max(0, Math.round(ms / 60_000));
  if (min < 1) return "<1 min";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  return `${h} h ${min % 60} min`;
}

function fmtInt(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

/** "values_at_risk" → "Values at risk". */
function humanizeAgent(name: string): string {
  const s = name.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function stripUiFields(p: unknown): unknown {
  if (!p || typeof p !== "object") return p;
  const { refineThread: _t, refineOpen: _o, ...rest } = p as Record<string, unknown>;
  return rest;
}

const TONE_BORDER: Record<Tone, string> = {
  red: "border-l-red-500",
  amber: "border-l-amber-400",
  ember: "border-l-ember-500",
  smoke: "border-l-smoke-500",
};
const TONE_CHIP: Record<Tone, string> = {
  red: "bg-red-600 text-white",
  amber: "bg-amber-400 text-black",
  ember: "bg-ember-600 text-white",
  smoke: "bg-smoke-600 text-smoke-100",
};
const TONE_TEXT: Record<Tone, string> = {
  red: "text-red-300",
  amber: "text-amber-300",
  ember: "text-ember-300",
  smoke: "text-smoke-200",
};

type Citations = {
  datasets?: Array<{ name?: string; version?: string | null; url?: string | null }>;
  models?: Array<{ name?: string; version?: string | null }>;
};

function CitationList({ c }: { c?: Citations }) {
  const ds = c?.datasets ?? [];
  const models = c?.models ?? [];
  if (!ds.length && !models.length) return null;
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-smoke-400">
        Sources
      </div>
      <ul className="mt-0.5 space-y-0.5">
        {ds.map((d, i) => (
          <li key={i} className="truncate">
            {d.url ? (
              <a href={d.url} target="_blank" rel="noreferrer" className="text-ember-300 hover:underline">
                {d.name}
              </a>
            ) : (
              d.name
            )}
            {d.version ? <span className="text-smoke-400"> · {d.version}</span> : null}
          </li>
        ))}
        {models.map((m, i) => (
          <li key={`m${i}`} className="text-smoke-400">
            model: {m.name}
            {m.version ? ` (${m.version})` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceList({ title, items }: { title: string; items: ReactNode[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-smoke-400">
        {title}
      </div>
      <ul className="mt-0.5 space-y-1">
        {items.map((it, i) => (
          <li key={i} className="leading-snug">
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

// --------------------------------------------------------------------------
// describe(): turn each interrupt type into the card's fields
// --------------------------------------------------------------------------

export function describe(item: PendingInterrupt): Described {
  const p = (item.interrupt.payload ?? {}) as Record<string, unknown>;
  switch (item.interrupt.type) {
    case "trigger_point_violation":
      return describeTrigger(p);
    case "evac_zone_change":
      return describeEvac(p as EvacZoneChangePayload);
    case "resource_recommendation":
      return describeResources(p);
    case "iap_approval":
      return describeIap(p);
    default:
      return {
        kind: String(item.interrupt.type).replace(/_/g, " "),
        tone: "smoke",
        priority: 9,
        title: "Agent requests a decision",
        stakes: [],
        confidence: typeof p.confidence === "number" ? p.confidence : null,
        evidence: null,
        evidenceCount: 0,
        approveLabel: "Approve",
        editor: null,
      };
  }
}

function describeTrigger(p: Record<string, unknown>): Described {
  const summary = (p.cone_summary ?? {}) as Record<string, number | undefined>;
  const hours = Number(p.hours_until);
  const prob = Number(p.prob);
  return {
    kind: "Trigger point breach",
    tone: "red",
    priority: 0,
    title: `Spread model projects trigger ${String(p.trigger ?? "?")} reached${
      Number.isFinite(hours) ? ` in ~${hours} h` : ""
    }`,
    subtitle: p.incident ? `Incident: ${String(p.incident)}` : undefined,
    stakes: [
      ...(Number.isFinite(hours)
        ? [{ label: "Time to breach", value: `~${hours} h`, tone: "red" as Tone }]
        : []),
      ...(Number.isFinite(prob)
        ? [{ label: "Probability", value: `${Math.round(prob * 100)}%` }]
        : []),
    ],
    // The probability is shown as a stake, not as "confidence": a 30 %
    // chance of breach is not a low-confidence proposal.
    confidence: null,
    source: "spread simulation (Monte Carlo)",
    evidence: (
      <EvidenceList
        title="Spread model"
        items={[
          summary.head_ros_fpm != null ? `Head rate of spread ${summary.head_ros_fpm} ft/min` : null,
          summary.flame_length_ft != null ? `Flame length ${summary.flame_length_ft} ft` : null,
          summary["24h_burn_area_km2"] != null
            ? `24 h burn area ${summary["24h_burn_area_km2"]} km² (${(Number(summary["24h_burn_area_km2"]) * 247.1).toFixed(0)} ac)`
            : null,
        ].filter(Boolean) as string[]}
      />
    ),
    evidenceCount: 3,
    approveLabel: "Acknowledge breach",
    editor: null,
  };
}

function describeEvac(p: EvacZoneChangePayload): Described {
  const proposed = String(p.proposed_status ?? "WARNING").toUpperCase();
  const isOrder = proposed === "ORDER";
  const impact = p.impact ?? {};
  const people = impact.human_displacement_estimate ?? p.population_estimate;
  const why: string[] = Array.isArray(p.why)
    ? p.why
    : p.rationale
      ? [p.rationale]
      : [];
  const source =
    p.rationale_source === "llm"
      ? "AI synthesis"
      : p.rationale_source === "synthetic_test"
        ? "synthetic test proposal"
        : "rule-based";
  return {
    kind: `PROPOSED evac ${proposed}`,
    tone: isOrder ? "red" : proposed === "WARNING" ? "amber" : "smoke",
    priority: isOrder ? 1 : 2,
    title: `${p.name ?? "Evacuation zone"}${p.jurisdiction ? ` · ${p.jurisdiction}` : ""}`,
    subtitle: `${p.current_status ?? "Current status unknown"} → PROPOSED ${proposed}`,
    stakes: [
      { label: "People displaced", value: `~${fmtInt(people)}`, tone: isOrder ? "red" : "amber" },
      ...(impact.residential_structures_estimate != null
        ? [{ label: "Homes", value: `~${fmtInt(impact.residential_structures_estimate)}` }]
        : []),
      ...(impact.egress_clear != null
        ? [
            {
              label: "Egress",
              value: impact.egress_clear
                ? "routes clear"
                : `at risk · ${impact.egress_blocked_edges ?? "?"} blocked`,
              tone: (impact.egress_clear ? "smoke" : "red") as Tone,
            },
          ]
        : []),
    ],
    confidence: null,
    source,
    evidence: <EvidenceList title="Why" items={why} />,
    evidenceCount: why.length,
    approveLabel: `Approve ${proposed}`,
    editor: "evac",
    expiresAt: p.expires_at,
  };
}

function describeResources(p: Record<string, unknown>): Described {
  const rec = (p.recommendation ?? {}) as Record<string, unknown>;
  const groups = RESOURCE_GROUPS.map((g) => ({
    g,
    lines: ((rec[g] as ResourceLine[] | undefined) ?? []).filter(
      (l) => (l.quantity ?? 0) > 0,
    ),
  }));
  const count = (g: string) =>
    groups
      .find((x) => x.g === g)
      ?.lines.reduce((s, l) => s + (l.quantity ?? 0), 0) ?? 0;
  const total = groups.reduce(
    (s, x) => s + x.lines.reduce((t, l) => t + (l.quantity ?? 0), 0),
    0,
  );
  const urgency = String(p.urgency ?? rec.urgency ?? "").toLowerCase();
  const fastest = groups
    .flatMap((x) => x.lines)
    .map((l) => l.arrival_window)
    .filter(Boolean)[0];
  const citations = p.citations as Citations | undefined;
  return {
    kind: `Resources${urgency ? ` · ${urgency} urgency` : ""}`,
    tone: urgency === "high" || urgency === "critical" ? "amber" : "ember",
    priority: urgency === "high" || urgency === "critical" ? 3 : 5,
    title: `RECOMMEND ${total} units for ${String(rec.incident_name ?? "this incident")}`,
    subtitle: rec.operational_period != null ? `Operational period ${rec.operational_period}` : undefined,
    stakes: [
      { label: "Engines & equipment", value: String(count("apparatus")) },
      { label: "Crews", value: String(count("crews")) },
      { label: "Aircraft", value: String(count("aircraft")) },
      { label: "Overhead", value: String(count("overhead")) },
    ],
    confidence: typeof p.confidence === "number" ? p.confidence : null,
    confidenceNote: fastest ? `First arrivals in ${fastest}.` : undefined,
    evidence: (
      <>
        {typeof rec.rationale_summary === "string" && (
          <p className="text-smoke-200">{rec.rationale_summary}</p>
        )}
        <EvidenceList
          title="Line items"
          items={groups.flatMap((x) =>
            x.lines.map((l) => (
              <span key={`${x.g}-${l.type}`}>
                <span className="font-semibold">
                  {l.quantity} × {l.type}
                </span>
                {l.arrival_window && (
                  <span className="text-smoke-400"> · arrives {l.arrival_window}</span>
                )}
                {l.rationale && (
                  <span className="block text-[10px] text-smoke-400">{l.rationale}</span>
                )}
              </span>
            )),
          )}
        />
        <CitationList c={citations} />
      </>
    ),
    evidenceCount: groups.reduce((s, x) => s + x.lines.length, 0),
    // The title already says RECOMMEND; the button stays short enough to
    // fit on one line in the 360 px column.
    approveLabel: "Approve",
    editor: "resources",
    expiresAt: typeof p.expires_at === "string" ? p.expires_at : undefined,
  };
}

function describeIap(p: Record<string, unknown>): Described {
  const draft = (p.draft ?? {}) as Record<string, unknown>;
  const dissent = (p.dissent_log as Array<Record<string, unknown>> | undefined) ?? [];
  const high = dissent.filter((d) => d.severity === "high");
  const medium = dissent.filter((d) => d.severity === "medium");
  const sections = (draft.sections ?? {}) as Record<string, unknown>;
  const citations = p.citations as Citations | undefined;
  const deterministic = citations?.models?.some((m) =>
    /deterministic/i.test(String(m.name)),
  );
  const objectives = (draft.objectives as string[] | undefined) ?? [];
  return {
    kind: `IAP DRAFT · ${String(p.form_type ?? draft.form ?? "ICS")}`,
    tone: "ember",
    priority: 4,
    title: `Sign the ${String(p.form_type ?? draft.form ?? "IAP")} DRAFT for ${String(
      sections.incident_name ?? "this incident",
    )}`,
    subtitle:
      draft.operational_period != null
        ? `Operational period ${draft.operational_period} · ${objectives.length} objectives`
        : undefined,
    stakes: [
      {
        label: "High-severity gaps",
        value: String(high.length),
        tone: high.length ? "red" : "smoke",
      },
      {
        label: "Medium gaps",
        value: String(medium.length),
        tone: medium.length ? "amber" : "smoke",
      },
    ],
    confidence: typeof p.confidence === "number" ? p.confidence : null,
    confidenceNote: high.length
      ? `${high.length} specialist input${high.length === 1 ? " is" : "s are"} missing or weak; see the gaps below.`
      : undefined,
    source: deterministic ? "deterministic synthesis" : "AI synthesis",
    evidence: (
      <>
        <EvidenceList
          title="Gaps the synthesis flagged"
          items={[...high, ...medium].map((d, i) => (
            <span key={i}>
              <span
                className={`mr-1 rounded px-1 text-[9px] font-bold uppercase ${
                  d.severity === "high" ? "bg-red-700 text-white" : "bg-amber-600 text-white"
                }`}
              >
                {String(d.severity)}
              </span>
              <span className="font-semibold">
                {humanizeAgent(String(d.source_agent ?? "agent"))}:
              </span>{" "}
              {d.claim_a === "(no output)"
                ? "did not return; sections that depend on it are preliminary."
                : String(d.claim_a ?? d.rationale ?? "")}
            </span>
          ))}
        />
        <EvidenceList
          title="Objectives"
          items={objectives.map((o) => <Markdown inline key={o}>{o}</Markdown>)}
        />
        <CitationList c={citations} />
        <p className="text-[10px] text-smoke-400">
          Full form on the IAP tab.
        </p>
      </>
    ),
    evidenceCount: high.length + medium.length + objectives.length,
    approveLabel: "Approve DRAFT",
    editor: "iap",
  };
}

// --------------------------------------------------------------------------
// Inline refine chat: talk with the IC agent about one evac suggestion
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
          <div className="text-[10px] italic text-smoke-400">
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
          className="flex-1 rounded bg-smoke-900 px-2 py-1 text-[11px] text-smoke-200 placeholder:text-smoke-400 focus:outline-none focus:ring-1 focus:ring-ember-500 disabled:opacity-50"
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
