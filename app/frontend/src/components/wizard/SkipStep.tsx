import { useState } from "react";
import { AlertTriangle, SkipForward, Undo2 } from "lucide-react";

/**
 * "Skip this step" — one control, used by every optional step.
 *
 * Two things make it honest rather than a shortcut.
 *
 * It states the consequence **before** the click, from text the server supplies alongside the
 * step list. A skip is a decision about the architecture — declining to deduplicate means two
 * spellings of one role stay two roles — and a control that only reveals that afterwards is
 * asking for a decision the user is not equipped to make.
 *
 * And it confirms. Not because a skip is dangerous (every one is reversible by running the real
 * step), but because for the grouping steps it *writes* something: skipping deduplication
 * commits one group per record, which cascades into everything above it exactly as a real
 * confirmation would. A single click that quietly invalidates downstream work is the wrong
 * shape for that.
 *
 * Once skipped it becomes an undo. Withdrawing the marker does not delete what was written —
 * the honest way back is to run the real step, which overwrites it — so the copy says so.
 */
export function SkipStep({
  label,
  consequence,
  kind,
  skipped,
  busy,
  onSkip,
  onUndo,
}: {
  label: string;
  /** What declining this step means, in the server's words. */
  consequence: string;
  kind: "identity" | "omission";
  skipped: boolean;
  busy?: boolean;
  onSkip: () => void;
  onUndo: () => void;
}) {
  const [confirming, setConfirming] = useState(false);

  if (skipped) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-[8px] border border-border bg-panel px-3 py-2">
        <SkipForward size={12} className="shrink-0 text-text-muted" />
        <p className="min-w-0 flex-1 text-[11.5px] leading-snug text-text-secondary">
          <strong className="text-text">Skipped.</strong> {consequence}
        </p>
        <button
          onClick={onUndo}
          disabled={busy}
          title={
            kind === "identity"
              ? "Mark the step as outstanding again. What the skip wrote stays until you run the step for real."
              : "Mark the step as outstanding again."
          }
          className="flex shrink-0 items-center gap-1 rounded-[6px] border border-border bg-card px-2 py-1 text-[10.5px] font-semibold text-accent hover:border-accent disabled:opacity-50"
        >
          <Undo2 size={10} /> Un-skip
        </button>
      </div>
    );
  }

  if (!confirming) {
    return (
      <button
        onClick={() => setConfirming(true)}
        disabled={busy}
        className="flex items-center gap-1.5 text-[11.5px] font-semibold text-text-muted underline decoration-dotted underline-offset-2 transition-colors hover:text-accent disabled:opacity-50"
      >
        <SkipForward size={11} /> Skip this step
      </button>
    );
  }

  return (
    <div className="rounded-[8px] border border-warning-border bg-warning-bg px-3 py-2.5">
      <div className="flex items-start gap-2">
        <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warning" />
        <div className="min-w-0">
          <p className="text-[12px] font-bold text-text">Skip {label.toLowerCase()}?</p>
          <p className="mt-0.5 text-[11.5px] leading-snug text-text-secondary">{consequence}</p>
          {kind === "identity" && (
            <p className="mt-1 text-[11px] leading-snug text-text-muted">
              This is a confirmation like any other, so anything already built on this step will
              be marked as out of date.
            </p>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <button
          onClick={() => {
            setConfirming(false);
            onSkip();
          }}
          disabled={busy}
          className="rounded-[6px] border border-brand bg-brand px-2.5 py-1 text-[11px] font-bold text-white hover:bg-brand-hover disabled:opacity-50"
        >
          Skip it
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="rounded-[6px] border border-border bg-card px-2.5 py-1 text-[11px] font-bold text-text hover:bg-panel"
        >
          Keep the step
        </button>
      </div>
    </div>
  );
}
