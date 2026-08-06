import type { ClusterOpportunity, RoleTask } from "../../types/workforce";

/**
 * Hover detail for a task-cluster cell: the task, its scores, and the actions inside it.
 *
 * Fixed to the viewport rather than to the cell, and flipped near an edge. The map sits
 * inside a scrolling panel and this is taller than most cells, so anchoring it to the cell
 * put it off-screen for anything in the lower half.
 *
 * Lifted out of TaskTreemap unchanged when the treemap was made generic — Work Design wants
 * exactly the same panel over the same data, and a second copy would drift.
 */
export function TreemapTooltip({
  task,
  actions,
  x,
  y,
}: {
  task: RoleTask;
  actions: ClusterOpportunity["actions"];
  x: number;
  y: number;
}) {
  const W = 340;
  const H = Math.min(340, 116 + actions.length * 30);
  const pad = 14;
  const left = x + pad + W > window.innerWidth - 8 ? Math.max(8, x - W - pad) : x + pad;
  const top = y + pad + H > window.innerHeight - 8 ? Math.max(8, y - H - pad) : y + pad;
  const t = task;

  return (
    <div
      className="pointer-events-none fixed z-50 rounded-[8px] border border-border bg-card px-3 py-2 shadow-[var(--shadow-elevated)]"
      style={{ left, top, width: W }}
    >
      <p className="text-[11.5px] font-bold leading-snug text-text">{t.name}</p>
      <p className="mt-0.5 text-[10.5px] leading-snug text-text-muted">{t.cluster}</p>
      <p className="mt-1 border-t border-border pt-1 text-[10.5px] text-text-secondary">
        <strong className="text-text">{t.proportion.toFixed(1)}%</strong> of the role
        {t.automation !== null && (
          <>
            {" · "}
            <strong className="text-text">{Math.round(t.automation)}%</strong> automatable
          </>
        )}
        {t.augmentation !== null && (
          <>
            {" · "}
            <strong className="text-text">{Math.round(t.augmentation)}%</strong> augmentable
          </>
        )}
      </p>
      {actions.length === 0 ? (
        <p className="mt-1.5 text-[10.5px] text-text-muted">
          {t.automation === null
            ? "This task's cluster has not been assessed."
            : "No action breakdown recorded for this cluster."}
        </p>
      ) : (
        <>
          <div className="mt-1.5 flex items-center gap-2 border-b border-border pb-0.5 text-[9.5px] font-extrabold uppercase tracking-wider text-text-muted">
            <span className="min-w-0 flex-1">Action</span>
            <span className="w-10 text-right">% task</span>
            <span className="w-9 text-right">Auto</span>
            <span className="w-9 text-right">Augm</span>
          </div>
          {actions.map((a) => (
            <div
              key={a.name}
              className="flex items-baseline gap-2 border-b border-border/50 py-0.5 last:border-0 text-[10.5px]"
            >
              <span className="min-w-0 flex-1 truncate text-text">{a.name}</span>
              <span className="w-10 shrink-0 text-right tabular-nums text-text-secondary">
                {a.pct_of_task.toFixed(0)}
              </span>
              <span className="w-9 shrink-0 text-right tabular-nums text-text-secondary">
                {Math.round(a.automation)}
              </span>
              <span className="w-9 shrink-0 text-right tabular-nums text-text-secondary">
                {Math.round(a.augmentation)}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
