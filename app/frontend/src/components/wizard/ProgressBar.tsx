import { AlertCircle, Loader2 } from "lucide-react";
import type { JobState } from "../../hooks/useJobStream";

/** Live progress for a running pipeline stage, driven by the WebSocket stream. */
export function ProgressBar({ job }: { job: JobState }) {
  if (job.error) {
    return (
      <div className="flex items-start gap-2 rounded-[10px] border border-brand-border bg-brand-bg px-4 py-3">
        <AlertCircle size={15} className="mt-0.5 shrink-0 text-brand" />
        <div className="min-w-0">
          <p className="text-[12.5px] font-semibold text-text">This stage failed</p>
          <p className="mt-0.5 break-words text-[12px] text-text-secondary">{job.error}</p>
        </div>
      </div>
    );
  }

  if (!job.running && !job.summary) return null;

  return (
    <div className="rounded-[10px] border border-border bg-panel px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2 text-[12.5px] font-semibold text-text">
          {job.running && <Loader2 size={13} className="shrink-0 animate-spin text-accent" />}
          <span className="truncate">{job.message || (job.running ? "Working…" : "Complete")}</span>
        </span>
        <span className="shrink-0 text-[11px] font-bold tabular-nums text-text-muted">
          {job.total > 0 ? `${job.current}/${job.total}` : ""}
          {job.running && job.elapsed > 0 && ` · ${formatElapsed(job.elapsed)}`}
        </span>
      </div>

      <div className="mt-2 h-2 overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-300"
          style={{ width: `${job.percent}%` }}
        />
      </div>

      {job.summary && !job.running && (
        <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
          {Object.entries(job.summary)
            .filter(([, v]) => typeof v === "number" || typeof v === "string")
            .map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-1.5">
                <dt className="text-[10.5px] font-semibold uppercase tracking-wide text-text-muted">
                  {k.replace(/_/g, " ")}
                </dt>
                <dd className="text-[12.5px] font-bold tabular-nums text-accent">{String(v)}</dd>
              </div>
            ))}
        </dl>
      )}
    </div>
  );
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}
