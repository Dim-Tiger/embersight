import {
  CircleCheck,
  CloudOff,
  Info,
  OctagonAlert,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

/**
 * One severity scale for every triage surface (Briefing, Threats,
 * Resources). Each level is carried three ways at once: colour, icon
 * shape, and a printed word. Under pressure, or for a colour-blind IC,
 * any one of the three is enough to read it.
 *
 * "degraded" is its own level on purpose: when a data source failed, the
 * UI must say "we don't know", never show a reassuring zero.
 */
export type Severity = "critical" | "warning" | "info" | "ok" | "degraded";

type SeverityStyle = {
  word: string;
  Icon: LucideIcon;
  /** Solid chip: background + foreground pair, AA on its own. */
  chip: string;
  /** Left accent border for cards. */
  edge: string;
  /** Icon/accent colour on a neutral panel. */
  accent: string;
};

export const SEVERITY: Record<Severity, SeverityStyle> = {
  critical: {
    word: "Critical",
    Icon: OctagonAlert,
    chip: "bg-sev-critical-bg text-sev-critical-fg",
    edge: "border-l-sev-critical-edge",
    accent: "text-sev-critical-edge",
  },
  warning: {
    word: "Warning",
    Icon: TriangleAlert,
    chip: "bg-sev-warning-bg text-sev-warning-fg",
    edge: "border-l-sev-warning-edge",
    accent: "text-sev-warning-edge",
  },
  info: {
    word: "Info",
    Icon: Info,
    chip: "bg-sev-info-bg text-sev-info-fg",
    edge: "border-l-sev-info-edge",
    accent: "text-sev-info-edge",
  },
  ok: {
    word: "OK",
    Icon: CircleCheck,
    chip: "bg-sev-ok-bg text-sev-ok-fg",
    edge: "border-l-sev-ok-edge",
    accent: "text-sev-ok-edge",
  },
  degraded: {
    word: "Data gap",
    Icon: CloudOff,
    chip: "bg-sev-degraded-bg text-sev-degraded-fg",
    edge: "border-l-sev-degraded-edge",
    accent: "text-sev-degraded-edge",
  },
};

/** Lower number = more urgent. Used to sort attention items. */
export const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  warning: 1,
  degraded: 2,
  info: 3,
  ok: 4,
};

export function SeverityBadge({
  level,
  word,
  size = "sm",
}: {
  level: Severity;
  /** Override the printed word (e.g. "Downwind"), keeping colour + icon. */
  word?: string;
  size?: "sm" | "md";
}) {
  const s = SEVERITY[level];
  const sizing =
    size === "md"
      ? "gap-1.5 px-2 py-1 text-[11px]"
      : "gap-1 px-1.5 py-0.5 text-[10px]";
  const icon = size === "md" ? "h-3.5 w-3.5" : "h-3 w-3";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded font-bold uppercase tracking-wider ${sizing} ${s.chip}`}
    >
      <s.Icon className={icon} aria-hidden />
      {word ?? s.word}
    </span>
  );
}
