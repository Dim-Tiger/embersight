import { CloudOff } from "lucide-react";
import { type ReactNode } from "react";
import { SEVERITY, type Severity } from "./Severity";

/**
 * A single big number with its unit spelled out. When `unavailable` is
 * set the tile shows "No data" instead of a number, so a failed source
 * can never be mistaken for a reassuring zero.
 */
export function StatTile({
  label,
  value,
  unit,
  hint,
  level,
  unavailable,
  icon,
}: {
  label: string;
  value: string | number;
  unit?: string;
  hint?: ReactNode;
  level?: Severity;
  /** Reason the data is missing, e.g. "HIFLD hospitals failed". */
  unavailable?: string | null;
  icon?: ReactNode;
}) {
  const edge = unavailable
    ? SEVERITY.degraded.edge
    : level
      ? SEVERITY[level].edge
      : "border-l-smoke-600";
  return (
    <div
      className={`min-w-0 rounded-md border border-l-4 border-smoke-700 bg-smoke-900/70 px-3 py-2.5 ${edge}`}
    >
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-smoke-300">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      {unavailable ? (
        <>
          <div className="mt-1 flex items-center gap-1.5 text-[18px] font-bold text-smoke-300">
            <CloudOff className="h-4 w-4" aria-hidden />
            No data
          </div>
          <div className="mt-0.5 text-[11px] leading-snug text-smoke-400">
            {unavailable}
          </div>
        </>
      ) : (
        <>
          <div className="mt-1 flex items-baseline gap-1">
            <span className="text-[24px] font-bold leading-none tabular-nums text-smoke-100">
              {value}
            </span>
            {unit && (
              <span className="text-[12px] font-semibold text-smoke-300">
                {unit}
              </span>
            )}
          </div>
          {hint && (
            <div className="mt-1 text-[11px] leading-snug text-smoke-400">
              {hint}
            </div>
          )}
        </>
      )}
    </div>
  );
}
