import { ChevronRight } from "lucide-react";
import { type ReactNode } from "react";

/**
 * Collapsible section built on <details>, so it works with the keyboard
 * and screen readers for free. Secondary detail lives in here, closed by
 * default, so the first screen of a tab stays short.
 */
export function Disclosure({
  title,
  meta,
  defaultOpen = false,
  children,
  tone = "panel",
}: {
  title: ReactNode;
  /** Right-aligned summary (counts, confidence) visible while closed. */
  meta?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  tone?: "panel" | "plain";
}) {
  return (
    <details
      open={defaultOpen}
      className={`group ${
        tone === "panel"
          ? "rounded-md border border-smoke-700 bg-smoke-800/40"
          : ""
      }`}
    >
      <summary
        className={`flex cursor-pointer list-none items-center gap-2 text-[13px] font-semibold text-smoke-200 hover:text-smoke-100 [&::-webkit-details-marker]:hidden ${
          tone === "panel" ? "px-4 py-2.5" : "py-1"
        }`}
      >
        <ChevronRight
          className="h-4 w-4 shrink-0 text-smoke-400 transition-transform group-open:rotate-90"
          aria-hidden
        />
        <span className="min-w-0 flex-1">{title}</span>
        {meta && (
          <span className="shrink-0 text-[11px] font-normal text-smoke-400">
            {meta}
          </span>
        )}
      </summary>
      <div className={tone === "panel" ? "space-y-3 border-t border-smoke-700 px-4 py-3" : "pt-1"}>
        {children}
      </div>
    </details>
  );
}

/** Small uppercase heading used inside sections. */
export function SectionLabel({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between gap-2">
      <h4 className="text-[11px] font-bold uppercase tracking-widest text-smoke-300">
        {children}
      </h4>
      {right && <span className="text-[11px] text-smoke-400">{right}</span>}
    </div>
  );
}
