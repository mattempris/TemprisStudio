import { Download, FileText, Pencil, Trash2 } from "lucide-react";
import type { DesignedJob, TargetProfile } from "../../types/workDesign";

/**
 * The designed jobs, and the accumulated work they hold — the two panels where results land.
 *
 * Deleting a job returns its hours to the pool. Nothing here does that explicitly: the pool is
 * `to_be − allocated`, so removing a job removes its contribution and the work reappears. It is
 * the conservation invariant doing the work rather than a second path that could disagree.
 */
export function DesignedJobList({
  jobs,
  target,
  unit,
  xlsxUrl,
  onEdit,
  onDelete,
}: {
  jobs: DesignedJob[];
  target: TargetProfile | null;
  unit: string;
  xlsxUrl: string;
  onEdit: (job: DesignedJob) => void;
  onDelete: (job: DesignedJob) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {/* ---- the accumulated to-be work ---- */}
      <section
        id="wd-target"
        className="scroll-mt-24 rounded-[var(--radius-modal)] border border-border bg-card shadow-modal"
      >
        <div className="border-b border-border px-4 py-3">
          <p className="text-[13px] font-bold text-text">Target task profile</p>
          <p className="mt-0.5 text-[11px] leading-snug text-text-muted">
            The work every designed job holds, together.
            {target && target.totals.oversight_hours_per_week > 0 && (
              <>
                {" "}
                <strong className="text-text-secondary">
                  {target.totals.oversight_hours_per_week.toFixed(1)} h
                </strong>{" "}
                of it is oversight the agents created.
              </>
            )}
          </p>
        </div>
        <div className="px-4 py-3">
          {!target || target.lines.length === 0 ? (
            <p className="py-8 text-center text-[12px] text-text-muted">
              Nothing designed yet. Allocate work into a job definition and it appears here.
            </p>
          ) : (
            <>
              <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] tabular-nums text-text-secondary">
                <span>
                  <strong className="text-text">{target.totals.jobs}</strong> jobs
                </span>
                <span>
                  <strong className="text-text">{target.totals.headcount}</strong> {unit}
                </span>
                <span>
                  <strong className="text-text">
                    {target.totals.hours_per_week.toLocaleString(undefined, {
                      maximumFractionDigits: 0,
                    })}
                  </strong>{" "}
                  h a week
                </span>
              </div>
              <div className="max-h-56 overflow-y-auto">
                {target.lines.map((l) => (
                  <div
                    key={l.key}
                    className="flex items-center gap-2 border-b border-border/60 py-1 last:border-0"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11.5px] text-text">{l.name}</span>
                      <span className="block truncate text-[10.5px] text-text-muted">
                        {l.origin === "agent_oversight" ? "oversight · " : ""}
                        {l.jobs.join(", ")}
                      </span>
                    </span>
                    <span className="shrink-0 text-right text-[11.5px] tabular-nums text-text-secondary">
                      {l.hours_per_week.toFixed(1)} h
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>

      {/* ---- the job definitions ---- */}
      <section
        id="wd-jobs"
        className="scroll-mt-24 rounded-[var(--radius-modal)] border border-border bg-card shadow-modal"
      >
        <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div>
            <p className="text-[13px] font-bold text-text">Job definitions ({jobs.length})</p>
            <p className="mt-0.5 text-[11px] text-text-muted">
              Load one to edit it, or delete it to return its work to the pool.
            </p>
          </div>
          {jobs.length > 0 && (
            <a
              href={xlsxUrl}
              className="flex shrink-0 items-center gap-1.5 rounded-[8px] border border-border bg-card px-2.5 py-1.5 text-[11.5px] font-semibold text-text-secondary transition-colors hover:border-accent hover:text-accent"
            >
              <Download size={11} /> XLSX
            </a>
          )}
        </div>
        <div className="px-4 py-3">
          {jobs.length === 0 ? (
            <p className="py-8 text-center text-[12px] text-text-muted">
              No job definitions yet.
            </p>
          ) : (
            <div className="max-h-56 overflow-y-auto">
              {jobs.map((j) => (
                <div
                  key={j.id}
                  className="flex items-center gap-2 border-b border-border/60 py-1.5 last:border-0"
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5">
                      <span className="min-w-0 truncate text-[11.5px] font-semibold text-text">
                        {j.title}
                      </span>
                      {j.stale && (
                        <span
                          title={j.stale_reason}
                          className="shrink-0 rounded-full border border-warning-border bg-warning-bg px-1.5 text-[9px] font-bold uppercase text-warning"
                        >
                          stale
                        </span>
                      )}
                    </span>
                    <span className="block truncate text-[10.5px] tabular-nums text-text-muted">
                      {j.headcount} {unit === "FTE" ? "people" : "holders"} ·{" "}
                      {j.capacity.assigned_hours_per_week.toFixed(1)}/
                      {j.capacity.capacity_hours_per_week.toFixed(1)} h ·{" "}
                      <span className={j.capacity.over_capacity ? "text-brand" : ""}>
                        {j.capacity.fill_pct?.toFixed(0)}%
                      </span>{" "}
                      · {j.tasks.length} lines
                    </span>
                  </span>
                  <button
                    onClick={() => onEdit(j)}
                    title="Load into the design panel"
                    className="shrink-0 text-text-muted hover:text-accent"
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    title="A role profile document — not built yet"
                    disabled
                    className="shrink-0 text-border"
                  >
                    <FileText size={12} />
                  </button>
                  <button
                    onClick={() => onDelete(j)}
                    title="Delete — its hours return to the pool"
                    className="shrink-0 text-text-muted hover:text-brand"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
