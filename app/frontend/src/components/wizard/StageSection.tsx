import type { ReactNode } from "react";
import { Check, ChevronDown, Lock } from "lucide-react";
import { cn } from "../../lib/cn";

export type StageState = "locked" | "active" | "complete";

interface StageSectionProps {
  index: number;
  id: string;
  title: string;
  description: string;
  state: StageState;
  /** One-line result shown when collapsed, e.g. "142 records -> 118 groups". */
  summary?: string;
  expanded: boolean;
  onToggle: () => void;
  lockedReason?: string;
  children: ReactNode;
}

/**
 * One numbered step in the vertical-scrolling wizard.
 *
 * A completed stage collapses to its one-line summary but stays expandable —
 * instructions.txt's flow is revisitable, so no step is ever a one-way door.
 */
export function StageSection({
  index,
  id,
  title,
  description,
  state,
  summary,
  expanded,
  onToggle,
  lockedReason,
  children,
}: StageSectionProps) {
  const locked = state === "locked";

  return (
    <section
      id={id}
      className={cn(
        "scroll-mt-24 rounded-[var(--radius-modal)] border bg-card shadow-modal transition-opacity",
        locked ? "border-border opacity-60" : "border-border",
      )}
    >
      <button
        type="button"
        onClick={locked ? undefined : onToggle}
        aria-expanded={expanded}
        disabled={locked}
        className={cn(
          "flex w-full items-start gap-4 p-6 text-left",
          !locked && "cursor-pointer",
        )}
      >
        <span
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[12px] font-bold",
            state === "complete" && "border-success bg-success text-white",
            state === "active" && "border-accent bg-accent text-white",
            locked && "border-border bg-panel text-text-muted",
          )}
        >
          {state === "complete" ? <Check size={14} /> : locked ? <Lock size={11} /> : index}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="text-[15px] font-bold text-text">{title}</span>
            {state === "complete" && (
              <span className="rounded-full border border-success-border bg-success-bg px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-success">
                Done
              </span>
            )}
          </span>
          <span className="mt-0.5 block text-[13px] leading-relaxed text-text-secondary">
            {locked && lockedReason ? lockedReason : description}
          </span>
          {state === "complete" && summary && !expanded && (
            <span className="mt-2 block text-[12.5px] font-semibold text-accent">{summary}</span>
          )}
        </span>

        {!locked && (
          <ChevronDown
            size={18}
            className={cn("mt-1 shrink-0 text-text-muted transition-transform", expanded && "rotate-180")}
          />
        )}
      </button>

      {expanded && !locked && <div className="border-t border-border px-6 py-5">{children}</div>}
    </section>
  );
}
