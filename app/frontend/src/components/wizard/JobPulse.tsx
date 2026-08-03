import { Loader2 } from "lucide-react";
import type { JobState } from "../../hooks/useJobStream";

/**
 * A small "still working" marker to sit beside the button that started a job.
 *
 * The elapsed count comes from the server's own heartbeats, not a local timer, so
 * it advancing is evidence the backend is alive and still talking — which is
 * exactly the question during a naming pass that shows no countable progress for
 * minutes at a time.
 */
export function JobPulse({ job, className }: { job: JobState; className?: string }) {
  if (!job.running) return null;
  // The server's own message names the phase — "Assessing stability of 5,193 items"
  // vs "Naming 21 task domains" — which is the difference between a spinner that
  // reassures and one that just spins. Truncated rather than dropped, since these
  // sit inline beside a button.
  const phase = (job.message || "").trim();
  return (
    <span className={`flex min-w-0 items-center gap-1.5 text-[11.5px] text-text-secondary ${className ?? ""}`}>
      <Loader2 size={12} className="shrink-0 animate-spin text-accent" />
      {phase && <span className="max-w-[22rem] truncate font-semibold text-text">{phase}</span>}
      {job.total > 0 && (
        <span className="shrink-0 font-semibold tabular-nums">
          {job.current}/{job.total}
        </span>
      )}
      {!phase && job.total === 0 && <span className="font-semibold">Working</span>}
      {job.elapsed > 0 && (
        <span className="shrink-0 tabular-nums text-text-muted">{formatElapsed(job.elapsed)}</span>
      )}
    </span>
  );
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds - m * 60)}s`;
}
