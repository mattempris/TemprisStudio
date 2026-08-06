import type { ReactNode } from "react";
import { Check, ChevronDown, Lock, SkipForward } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * `skipped` is its own state, not a flavour of `complete`.
 *
 * A skipped step has often produced exactly the artefact a completed one would — the identity
 * grouping — so by the usual test it *is* complete. But the badge is what a reader trusts when
 * they come back to a project in a month, and "Done" over a step nobody ran is the one claim
 * this wizard must not make.
 */
export type StageState = "locked" | "active" | "complete" | "skipped";

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
  /** Settings that belong to this step but do not depend on its inputs — shown
   *  even while the step is locked, so a template or framework can be prepared
   *  before the pipeline reaches the step that consumes it. */
  config?: ReactNode;
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
  config,
}: StageSectionProps) {
  const locked = state === "locked";
  // A locked step is still worth opening when it carries settings; without this
  // the profile template and evaluation framework were unreachable until the
  // whole hierarchy was built, which is backwards — they are inputs to it.
  const expandable = !locked || !!config;

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
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expanded}
        disabled={!expandable}
        className={cn(
          "flex w-full items-start gap-4 p-6 text-left",
          expandable && "cursor-pointer",
        )}
      >
        <span
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[12px] font-bold",
            state === "complete" && "border-success bg-success text-white",
            state === "skipped" && "border-border bg-panel text-text-muted",
            state === "active" && "border-accent bg-accent text-white",
            locked && "border-border bg-panel text-text-muted",
          )}
        >
          {state === "complete" ? (
            <Check size={14} />
          ) : state === "skipped" ? (
            <SkipForward size={12} />
          ) : locked ? (
            <Lock size={11} />
          ) : (
            index
          )}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="text-[15px] font-bold text-text">{title}</span>
            {state === "complete" && (
              <span className="rounded-full border border-success-border bg-success-bg px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-success">
                Done
              </span>
            )}
            {state === "skipped" && (
              <span className="rounded-full border border-border bg-panel px-2 py-0.5 text-[9px] font-extrabold uppercase tracking-wider text-text-muted">
                Skipped
              </span>
            )}
          </span>
          <span className="mt-0.5 block text-[13px] leading-relaxed text-text-secondary">
            {locked && lockedReason ? lockedReason : description}
          </span>
          {(state === "complete" || state === "skipped") && summary && !expanded && (
            <span
              className={cn(
                "mt-2 block text-[12.5px] font-semibold",
                state === "skipped" ? "text-text-muted" : "text-accent",
              )}
            >
              {summary}
            </span>
          )}
        </span>

        {expandable && (
          <ChevronDown
            size={18}
            className={cn("mt-1 shrink-0 text-text-muted transition-transform", expanded && "rotate-180")}
          />
        )}
      </button>

      {expanded && (
        <div className="border-t border-border px-6 py-5">
          {config}
          {locked ? (
            config && (
              <p className="mt-4 rounded-[10px] border border-border bg-panel px-4 py-3 text-[12.5px] text-text-secondary">
                {lockedReason} The settings above can be prepared now.
              </p>
            )
          ) : (
            <div className={config ? "mt-4" : undefined}>{children}</div>
          )}
        </div>
      )}
    </section>
  );
}
